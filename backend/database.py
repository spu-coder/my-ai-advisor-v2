import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

# ------------------------------------------------------------
# إعداد اتصال قاعدة البيانات
# ------------------------------------------------------------
DEFAULT_DATABASE_URL = "postgresql+psycopg://advisor:advisor@postgres:5432/advisor_db"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)

connect_args = {}

if DATABASE_URL.startswith("sqlite"):
    db_path = os.path.dirname(DATABASE_URL.replace("sqlite:///", ""))
    if db_path:
        os.makedirs(db_path, exist_ok=True)
    connect_args = {"check_same_thread": False}

ENGINE = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True) # معرف الطالب/المستخدم (الرقم الجامعي)
    full_name = Column(String)
    hashed_password = Column(String) # حقل كلمة المرور المشفرة (كلمة سر النظام الجامعي)
    role = Column(String, default="student") # طالب، إداري
    email = Column(String, unique=True, nullable=True) # أصبح اختياري
    university_password = Column(String, nullable=True) # كلمة سر النظام الجامعي (مشفرة)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_data_sync = Column(DateTime, nullable=True) # آخر مرة تم فيها جمع البيانات من النظام الجامعي

    # يمكن الآن استخدام relationship مع الجداول الأخرى في نفس قاعدة البيانات
    progress_records = relationship("ProgressRecord", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")
    chat_messages = relationship("ChatMessage", back_populates="user", cascade="all, delete-orphan")

# ------------------------------------------------------------
# قاعدة بيانات تقدم الطلاب (Progress DB)
# ------------------------------------------------------------
# استخدام نفس Base للجداول الموحدة
class ProgressRecord(Base):
    __tablename__ = "progress_records"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.user_id"), index=True)  # يمكن الآن استخدام ForeignKey
    course_code = Column(String)
    grade = Column(String)
    hours = Column(Integer)
    semester = Column(String)
    course_name = Column(String, nullable=True) # اسم المقرر
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # يمكن الآن استخدام relationship
    user = relationship("User", back_populates="progress_records")

class StudentAcademicInfo(Base):
    """معلومات أكاديمية شاملة للطالب من النظام الجامعي"""
    __tablename__ = "student_academic_info"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True, unique=True)  # الرقم الجامعي
    gpa = Column(Float, nullable=True)  # المعدل التراكمي
    total_hours = Column(Integer, nullable=True)  # إجمالي الساعات المطلوبة
    completed_hours = Column(Integer, nullable=True)  # الساعات المكتملة
    remaining_hours = Column(Integer, nullable=True)  # الساعات المتبقية
    academic_status = Column(String, nullable=True)  # الحالة الأكاديمية
    current_semester = Column(String, nullable=True)  # الفصل الحالي
    raw_data = Column(JSON, nullable=True)  # البيانات الخام من النظام الجامعي
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class RemainingCourse(Base):
    """المقررات المتبقية للتسجيل"""
    __tablename__ = "remaining_courses"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    course_code = Column(String, index=True)
    course_name = Column(String, nullable=True)
    hours = Column(Integer, nullable=True)
    prerequisites = Column(String, nullable=True)  # المتطلبات السابقة
    semester = Column(String, nullable=True)  # الفصل المقترح
    raw_data = Column(JSON, nullable=True)  # البيانات الخام
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# ------------------------------------------------------------
# قاعدة بيانات الإشعارات (Notifications DB)
# ------------------------------------------------------------
# استخدام نفس Base للجداول الموحدة
class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.user_id"), index=True)  # يمكن الآن استخدام ForeignKey
    message = Column(String)
    type = Column(String) # تنبيه، إشعار، توصية
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # يمكن الآن استخدام relationship
    user = relationship("User", back_populates="notifications")


class ChatMessage(Base):
    """سجل رسائل الدردشة للحفاظ على السياق"""
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.user_id"), index=True)
    role = Column(String)  # user / assistant
    content = Column(Text)
    intent = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="chat_messages")

# ------------------------------------------------------------
# وظائف التهيئة
# ------------------------------------------------------------

def init_db():
    # إنشاء جميع الجداول في قاعدة البيانات الموحدة
    Base.metadata.create_all(bind=ENGINE)

# إنشاء SessionLocal موحد
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=ENGINE)

def get_db():
    """دالة موحدة للحصول على جلسة قاعدة البيانات"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# إبقاء الأسماء القديمة للتوافق مع الكود الموجود
def get_users_session():
    """دالة للحصول على جلسة قاعدة البيانات (للتوافق)"""
    return get_db()

def get_progress_session():
    """دالة للحصول على جلسة قاعدة البيانات (للتوافق)"""
    return get_db()

def get_notifications_session():
    """دالة للحصول على جلسة قاعدة البيانات (للتوافق)"""
    return get_db()

# تهيئة قواعد البيانات عند استيراد الملف لأول مرة
init_db()
