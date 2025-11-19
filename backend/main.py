import os
import sys
from fastapi import FastAPI, Depends, HTTPException, status, Request, Query
import logging
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from typing import Annotated, Dict, Any, Optional
from pydantic import BaseModel, Field, field_validator

# إضافة مسار backend إلى sys.path للسماح بالاستيراد المطلق
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# استيراد الخدمات والنماذج
from logging_config import setup_logging
from database import get_users_session, get_progress_session, get_notifications_session, init_db, ChatMessage
from security import get_current_user, get_current_admin_user
from security_middleware import (
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    RequestSizeMiddleware,
    JWTAuthMiddleware,
    WAFMiddleware,
    InputSanitizationMiddleware,
    AuditLoggingMiddleware,
    sanitize_string,
    validate_user_id,
)
from services import users_service, progress_service, notifications_service, documents_service, graph_service, llm_service
from services.users_service import StudentCreate, AdminCreate, UserLogin, Token

# ------------------------------------------------------------
# إعداد التسجيل (Logging)
# ------------------------------------------------------------
setup_logging(logging.INFO)
logger = logging.getLogger("API_GATEWAY")

# ------------------------------------------------------------
# تهيئة التطبيق
# ------------------------------------------------------------
app = FastAPI(
    title="Smart Academic Advisor API Gateway",
    description="API Gateway and Request Router for the Microservices-based Academic Advisor System.",
    version="1.0.0",
    swagger_ui_init_oauth={
        "usePkceWithAuthorizationCodeGrant": False,
    },
)

# تهيئة قواعد البيانات
init_db()

# إعداد CORS
origins = [
    "http://localhost:8501",  # واجهة Streamlit
    "http://127.0.0.1:8501",
]

# Add security middlewares (outermost first when processing requests)
# التنفيذ الحقيقي للطبقات: RateLimit → RequestSize → WAF → JWT → Sanitization → Security Headers → Audit → CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuditLoggingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(InputSanitizationMiddleware)
app.add_middleware(
    JWTAuthMiddleware,
    protected_paths=(
        "/chat",
        "/users",
        "/progress",
        "/notifications",
        "/documents",
        "/graph",
    ),
)
app.add_middleware(WAFMiddleware)
app.add_middleware(RequestSizeMiddleware)
app.add_middleware(RateLimitMiddleware)

# ------------------------------------------------------------
# نماذج Pydantic للطلبات
# ------------------------------------------------------------

class ChatRequest(BaseModel):
    """Chat request model with input validation / نموذج طلب الدردشة مع التحقق من المدخلات"""
    question: str = Field(..., min_length=1, max_length=2000, description="User question / سؤال المستخدم")
    user_id: str = Field(..., min_length=1, max_length=50, description="User ID for personalized context / معرف المستخدم للسياق الشخصي")
    
    @field_validator('question')
    @classmethod
    def validate_question(cls, v):
        """Sanitize and validate question input"""
        if not v or not v.strip():
            raise ValueError("Question cannot be empty")
        return sanitize_string(v, max_length=2000)
    
    @field_validator('user_id')
    @classmethod
    def validate_user_id(cls, v):
        """Validate user_id format"""
        if not validate_user_id(v):
            raise ValueError("Invalid user_id format")
        return v

class ProgressRecordCreate(BaseModel):
    user_id: str
    course_code: str
    grade: str
    hours: int
    semester: str

class GPASimulationRequest(BaseModel):
    current_gpa: Optional[float] = Field(
        default=None,
        description="المعدل التراكمي الحالي (يتم حسابه تلقائياً إذا تُرك فارغاً)",
    )
    current_hours: Optional[int] = Field(
        default=None,
        description="اجمالي الساعات المكتملة (يتم حسابه تلقائياً إذا تُرك فارغاً)",
    )
    new_courses: Dict[str, int] = Field(..., description="{course_code: hours}")
    expected_grades: Dict[str, str] = Field(..., description="{course_code: grade}")

class SyncDataRequest(BaseModel):
    password: str = Field(..., description="كلمة سر النظام الجامعي")


# ------------------------------------------------------------
# Helpers / وظائف مساعدة
# ------------------------------------------------------------

def _get_chat_history(db_session: Session, user_id: str, limit: int = 10):
    """Fetch latest chat messages for the given user."""
    records = (
        db_session.query(ChatMessage)
        .filter(ChatMessage.user_id == user_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(records))


def _serialize_chat_history(records):
    history = []
    for record in records:
        history.append(
            {
                "role": record.role,
                "content": record.content,
                "intent": record.intent,
                "timestamp": record.created_at.isoformat() if record.created_at else None,
            }
        )
    return history


def _persist_chat_exchange(db_session: Session, user_id: str, question: str, answer: str, intent: Optional[str]):
    """Store the user/assistant messages for conversation continuity."""
    try:
        user_msg = ChatMessage(
            user_id=user_id,
            role="user",
            content=question,
            intent=intent,
        )
        assistant_msg = ChatMessage(
            user_id=user_id,
            role="assistant",
            content=answer,
            intent=intent,
        )
        db_session.add_all([user_msg, assistant_msg])
        db_session.commit()
    except Exception:
        db_session.rollback()
        logger.exception("Failed to persist chat exchange for user %s", user_id)

# ------------------------------------------------------------
# مسارات الأمان (Authentication & Authorization)
# ------------------------------------------------------------

@app.post("/register/student", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
def register_student(student_data: StudentCreate, db: Annotated[Session, Depends(get_users_session)]):
    """تسجيل طالب جديد (يتطلب التحقق من النظام الجامعي)."""
    logger.info(f"Attempting to register student: {student_data.user_id}")
    try:
        new_user = users_service.create_student(db, student_data)
        logger.info(f"Student registered successfully: {new_user['user_id']}")
        return new_user
    except HTTPException as e:
        logger.error(f"Registration failed for student {student_data.user_id}: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error during student registration: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="خطأ داخلي في تسجيل الطالب")

@app.post("/register/admin", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
def register_admin(
    admin_data: AdminCreate,
    current_admin: Annotated[users_service.User, Depends(get_current_admin_user)],
    db: Annotated[Session, Depends(get_users_session)]
):
    """إنشاء حساب أدمن جديد (يحتاج موافقة من أدمن رئيسي)."""
    logger.warning(f"Admin {current_admin.user_id} attempting to create new admin: {admin_data.user_id}")
    try:
        new_user = users_service.create_admin(db, admin_data, current_admin)
        logger.warning(f"Admin created successfully: {new_user['user_id']} by {current_admin.user_id}")
        return new_user
    except HTTPException as e:
        logger.error(f"Admin creation failed: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error during admin creation: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="خطأ داخلي في إنشاء حساب الأدمن")

@app.post("/register/admin/initial", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
def register_initial_admin(
    admin_data: AdminCreate,
    db: Annotated[Session, Depends(get_users_session)]
):
    """إنشاء حساب أدمن أولي (فقط إذا لم يكن هناك أدمن موجود)."""
    # التحقق من وجود أدمن موجود
    existing_admin = db.query(users_service.User).filter(users_service.User.role == "admin").first()
    if existing_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="يوجد أدمن موجود بالفعل. يجب تسجيل الدخول كأدمن لإنشاء حسابات جديدة."
        )
    
    logger.warning(f"Creating initial admin account: {admin_data.user_id}")
    try:
        # إنشاء حساب الأدمن مباشرة بدون الحاجة لموافقة
        from security import get_password_hash
        
        # التحقق من أن المعرف غير مستخدم
        if db.query(users_service.User).filter(users_service.User.user_id == admin_data.user_id).first():
            raise HTTPException(status_code=400, detail="معرف المستخدم مسجل بالفعل")
        
        # التحقق من أن البريد الإلكتروني غير مستخدم
        if db.query(users_service.User).filter(users_service.User.email == admin_data.email).first():
            raise HTTPException(status_code=400, detail="البريد الإلكتروني مسجل بالفعل")
        
        # تشفير كلمة المرور
        hashed_password = get_password_hash(admin_data.password)
        
        db_user = users_service.User(
            user_id=admin_data.user_id,
            full_name=admin_data.full_name,
            email=admin_data.email,
            hashed_password=hashed_password,
            university_password=None,
            role="admin"
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        
        logger.warning(f"Initial admin created successfully: {db_user.user_id}")
        return {
            "user_id": db_user.user_id, 
            "full_name": db_user.full_name, 
            "email": db_user.email, 
            "role": db_user.role
        }
    except HTTPException as e:
        logger.error(f"Initial admin creation failed: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error during initial admin creation: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="خطأ داخلي في إنشاء حساب الأدمن الأولي")

@app.post("/token", response_model=Token)
def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_users_session)]
):
    """تسجيل الدخول والحصول على رمز الوصول (JWT) - OAuth2 password flow."""
    logger.info(f"Attempting login with identifier: {form_data.username}")
    try:
        token_data = users_service.login_for_access_token(db, form_data.username, form_data.password, allow_demo=False)
        logger.info(f"Login successful: {form_data.username}, Demo: {token_data.is_demo}")
        return token_data
    except HTTPException as e:
        logger.warning(f"Login failed for {form_data.username}: {e.detail}")
        raise e

@app.post("/token/json", response_model=Token)
def login_for_access_token_json(
    user_data: UserLogin,
    db: Annotated[Session, Depends(get_users_session)],
    allow_demo: bool = Query(False, description="السماح بالوضع التجريبي")
):
    """تسجيل الدخول والحصول على رمز الوصول (JWT) - JSON format فقط."""
    logger.info(f"Attempting login with identifier: {user_data.identifier}, allow_demo: {allow_demo}")
    try:
        # تنظيف المدخلات
        identifier = user_data.identifier.strip() if user_data.identifier else ""
        password = user_data.password if user_data.password else ""
        
        if not identifier or not password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="يجب إدخال المعرف وكلمة المرور"
            )
        
        token_data = users_service.login_for_access_token(db, identifier, password, allow_demo)
        logger.info(f"Login successful: {identifier}, Demo: {token_data.is_demo}")
        return token_data
    except HTTPException as e:
        logger.warning(f"Login failed for {user_data.identifier}: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected error during login: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="حدث خطأ غير متوقع أثناء تسجيل الدخول"
        )

@app.get("/users/me", response_model=Dict[str, Any])
def read_users_me(current_user: Annotated[users_service.User, Depends(get_current_user)]):
    """الحصول على معلومات المستخدم الحالي (مسار محمي)."""
    result = {
        "user_id": current_user.user_id, 
        "full_name": current_user.full_name, 
        "email": getattr(current_user, 'email', None), 
        "role": current_user.role
    }
    if hasattr(current_user, 'is_demo'):
        result["is_demo"] = current_user.is_demo
    return result

@app.post("/users/sync-data", response_model=Dict[str, Any])
def sync_student_data(
    sync_request: SyncDataRequest,
    current_user: Annotated[users_service.User, Depends(get_current_user)],
    db_users: Annotated[Session, Depends(get_users_session)],
    db_progress: Annotated[Session, Depends(get_progress_session)]
):
    """جمع بيانات الطالب من النظام الجامعي وحفظها (محمي)."""
    # التحقق من الوضع التجريبي
    if hasattr(current_user, 'is_demo') and current_user.is_demo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="الوضع التجريبي لا يدعم جمع البيانات الشخصية"
        )
    
    # التحقق من أن المستخدم طالب
    if current_user.role != "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="هذه الميزة متاحة للطلاب فقط"
        )
    
    logger.info(f"Syncing data for student: {current_user.user_id}")
    
    try:
        result = users_service.sync_student_data_from_university(
            db_users, db_progress, current_user.user_id, sync_request.password
        )
        
        if result.get('success'):
            logger.info(f"Data sync successful for student: {current_user.user_id}")
            return result
        else:
            logger.error(f"Data sync failed for student {current_user.user_id}: {result.get('error')}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.get('error', 'فشل جمع البيانات')
            )
    except Exception as e:
        logger.error(f"Error syncing data for student {current_user.user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"خطأ في جمع البيانات: {str(e)}"
        )

# ------------------------------------------------------------
# مسارات الخدمات (مع تطبيق الأمان)
# ------------------------------------------------------------

# مسار الدردشة (محمي)
@app.post("/chat", response_model=Dict[str, Any])
async def chat_with_advisor(
    chat_request: ChatRequest,
    current_user: Annotated[users_service.User, Depends(get_current_user)],
    db_users: Annotated[Session, Depends(get_users_session)],
    db_progress: Annotated[Session, Depends(get_progress_session)],
    db_notifications: Annotated[Session, Depends(get_notifications_session)],
):
    """
    Main chat endpoint (Agentic RAG).
    / مسار الدردشة الرئيسي (Agentic RAG).
    
    Args:
        chat_request: Chat request with question and user_id
        current_user: Authenticated user from JWT token
        db_users: Users database session
        db_progress: Progress database session
        db_notifications: Notifications database session
        
    Returns:
        Dict containing answer, source, and intent
        
    Raises:
        HTTPException: If authorization fails or processing error occurs
    """
    logger.info(f"Chat request from user {current_user.user_id}: {chat_request.question[:100]}...")
    
    # Authorization check: verify user_id matches authenticated user
    # التحقق من التفويض: التحقق من تطابق معرف المستخدم مع المستخدم المصادق عليه
    if chat_request.user_id != current_user.user_id:
        logger.warning(f"Authorization failed: user {current_user.user_id} tried to query for {chat_request.user_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Cannot query for another user's data / لا يمكن الاستعلام عن بيانات مستخدم آخر"
        )
    
    # Check if demo mode
    # التحقق من الوضع التجريبي
    is_demo = hasattr(current_user, 'is_demo') and current_user.is_demo
    if is_demo:
        logger.info(f"Demo user {current_user.user_id} using chat - limited functionality")
    
    # جلسة الإشعارات متوفرة للاستخدام المستقبلي
    _ = db_notifications

    try:
        chat_history_records = []
        if not is_demo:
            chat_history_records = _get_chat_history(db_users, current_user.user_id, limit=10)
        chat_history = _serialize_chat_history(chat_history_records)

        # إعداد الخدمات
        services = {
            "documents": documents_service,
            "progress": progress_service,
            "progress_db": db_progress,
            "users_db": db_users,
            "graph": graph_service
        }
        
        # استخدام user_id فعال (None للوضع التجريبي)
        effective_user_id = chat_request.user_id if not is_demo else None
        
        # استدعاء الدالة غير المتزامنة مباشرة
        response_obj = await llm_service.process_agentic_query(
            question=chat_request.question,
            user_id=effective_user_id,
            services=services,
            is_demo=is_demo,
            chat_history=chat_history
        )
        
        # تحويل الاستجابة إلى قاموس
        response = {
            "answer": response_obj.answer,
            "source": response_obj.source,
            "intent": response_obj.intent
        }
        
        logger.info(f"Chat response generated for user {current_user.user_id}. Intent: {response.get('intent')}")

        if not is_demo and response_obj.intent != "clarify":
            _persist_chat_exchange(
                db_users,
                current_user.user_id,
                chat_request.question,
                response_obj.answer,
                response_obj.intent,
            )
        
        # Add demo warning
        # إضافة تحذير للوضع التجريبي
        if is_demo:
            response['demo_warning'] = "⚠️ أنت في الوضع التجريبي. الإجابات لا تعتمد على بياناتك الشخصية."
        
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing chat request for user {current_user.user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Error processing chat request / خطأ في معالجة طلب الدردشة"
        )

# مسارات تقدم الطلاب (محمية)
@app.post("/progress/record", response_model=Dict[str, Any])
def record_progress(
    record: ProgressRecordCreate,
    current_user: Annotated[users_service.User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_progress_session)],
):
    """تسجيل مقرر مكتمل (محمي)."""
    if record.user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot record progress for another user")
    
    logger.info(f"Recording progress for user {current_user.user_id}: {record.course_code}")
    return progress_service.record_progress(db, record.model_dump())

@app.get("/progress/analyze/{user_id}", response_model=Dict[str, Any])
def analyze_progress(
    user_id: str,
    current_user: Annotated[users_service.User, Depends(get_current_user)],
    db_progress: Annotated[Session, Depends(get_progress_session)],
    db_users: Annotated[Session, Depends(get_users_session)],
):
    """تحليل التقدم الأكاديمي (محمي)."""
    # التحقق من الوضع التجريبي
    if hasattr(current_user, 'is_demo') and current_user.is_demo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="الوضع التجريبي لا يدعم تحليل التقدم الشخصي"
        )
    
    if user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot analyze progress for another user")
    
    # التحقق من أن المستخدم طالب
    if current_user.role != "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="هذه الميزة متاحة للطلاب فقط"
        )
    
    logger.info(f"Analyzing progress for user {user_id}")
    return progress_service.analyze_progress(db_progress, db_users, user_id)

@app.post("/progress/simulate-gpa", response_model=Dict[str, Any])
def simulate_gpa(
    simulation_request: GPASimulationRequest,
    current_user: Annotated[users_service.User, Depends(get_current_user)],
    db_progress: Annotated[Session, Depends(get_progress_session)],
):
    """محاكاة المعدل التراكمي (محمي)."""
    logger.info(f"Simulating GPA for user {current_user.user_id}")
    return progress_service.simulate_gpa(
        db_progress,
        current_user.user_id,
        simulation_request.model_dump(),
    )

# مسارات الإشعارات (محمية)
@app.get("/notifications/{user_id}", response_model=list[Dict[str, Any]])
def get_user_notifications(
    user_id: str,
    current_user: Annotated[users_service.User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_notifications_session)],
):
    """الحصول على إشعارات المستخدم (محمي)."""
    if user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot view another user's notifications")
    
    logger.info(f"Fetching notifications for user {user_id}")
    return notifications_service.get_notifications(db, user_id)

# مسارات المستندات (محمية - للإداريين فقط)
@app.post("/documents/ingest", response_model=Dict[str, Any])
def ingest_documents_route(current_admin: Annotated[users_service.User, Depends(get_current_admin_user)]):
    """فهرسة المستندات (محمي للإداريين)."""
    logger.warning(f"Admin user {current_admin.user_id} is initiating document ingestion.")
    try:
        return documents_service.ingest_documents()
    except Exception as e:
        logger.error(f"Error during document ingestion: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error during document ingestion")

# مسارات الرسم البياني (محمية - للإداريين فقط)
@app.post("/graph/ingest", response_model=Dict[str, Any])
def ingest_graph_data_route(current_admin: Annotated[users_service.User, Depends(get_current_admin_user)]):
    """فهرسة بيانات الرسم البياني (محمي للإداريين)."""
    logger.warning(f"Admin user {current_admin.user_id} is initiating graph data ingestion.")
    try:
        return graph_service.ingest_graph_data()
    except Exception as e:
        logger.error(f"Error during graph data ingestion: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error during graph data ingestion")

@app.get("/graph/skills/{course_code}", response_model=Dict[str, Any])
def get_skills_for_course_route(course_code: str, current_user: Annotated[users_service.User, Depends(get_current_user)]):
    """الحصول على المهارات لمقرر معين (محمي)."""
    logger.info(f"User {current_user.user_id} querying skills for course {course_code}")
    try:
        skills = graph_service.get_skills_for_course(course_code)
        return {"course_code": course_code, "skills": skills}
    except Exception as e:
        logger.error(f"Error querying graph for course {course_code}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error querying graph data")

# ------------------------------------------------------------
# مسار فحص الصحة (Health Check)
# ------------------------------------------------------------

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "API Gateway"}
