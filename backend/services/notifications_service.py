import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from database import Notification
from config_manager import get_config
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

# ------------------------------------------------------------
# نماذج Pydantic
# ------------------------------------------------------------
class NotificationCreate(BaseModel):
    user_id: str
    message: str
    type: Optional[str] = "alert" # alert, recommendation, info

class NotificationInDB(NotificationCreate):
    id: int
    is_read: bool
    created_at: datetime
    class Config:
        orm_mode = True

# ------------------------------------------------------------
# وظائف الخدمة
# ------------------------------------------------------------

def create_notification(db: Session, notification: NotificationCreate):
    db_notification = Notification(**notification.dict())
    db.add(db_notification)
    db.commit()
    db.refresh(db_notification)
    return db_notification

def get_notifications(db: Session, user_id: str, skip: int = 0, limit: int = 10) -> List[Dict[str, Any]]:
    """الحصول على إشعارات المستخدم."""
    try:
        notifications = db.query(Notification).filter(
            Notification.user_id == user_id
        ).order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()
        
        return [
            {
                "id": notif.id,
                "user_id": notif.user_id,
                "message": notif.message,
                "type": notif.type,
                "is_read": notif.is_read,
                "created_at": notif.created_at.isoformat() if notif.created_at else None
            }
            for notif in notifications
        ]
    except Exception as e:
        return []

def mark_notification_as_read(db: Session, notification_id: int):
    notification = db.query(Notification).filter(Notification.id == notification_id).first()
    if notification:
        notification.is_read = True
        db.commit()
        db.refresh(notification)
        return notification
    return None

def check_gpa_warning(db: Session, user_id: str, current_gpa: float):
    """إضافة إشعار تحذيري إذا كان المعدل التراكمي أقل من الحد المحدد في التكوين."""
    config = get_config("notifications", {})
    warning_threshold = config.get("gpa_warning_threshold", 2.0)
    warning_message = config.get("low_gpa_message", f"تنبيه: معدلك التراكمي أقل من الحد الأدنى المسموح به ({warning_threshold}). يرجى مراجعة مرشدك الأكاديمي.")
    
    if current_gpa < warning_threshold:
        # التحقق مما إذا كان هناك إشعار تحذيري حديث لتجنب التكرار
        recent_alert = db.query(Notification).filter(
            Notification.user_id == user_id,
            Notification.type == "alert",
            Notification.message == warning_message,
            Notification.created_at > datetime.utcnow() - timedelta(days=7) # تحذير واحد في الأسبوع
        ).first()
        
        if not recent_alert:
            create_notification(db, NotificationCreate(user_id=user_id, message=warning_message, type="alert"))
