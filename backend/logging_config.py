import logging
import logging.config
import os

def setup_logging(log_level=logging.INFO):
    """
    إعداد نظام التسجيل (Logging) للمشروع.
    يتم إعداد التسجيل لكتابة logs إلى ملف وإلى وحدة التحكم (Console).
    """
    
    # التأكد من وجود مجلد logs
    log_dir = os.getenv("LOG_DIR", "/app/logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, "app.log")

    LOGGING_CONFIG = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "level": log_level,
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "standard",
                "filename": log_file_path,
                "maxBytes": 1024 * 1024 * 5,  # 5 MB
                "backupCount": 5,
                "level": logging.WARNING, # سجل التحذيرات والأخطاء فقط في الملف
            },
        },
        "loggers": {
            "": {  # Root logger
                "handlers": ["console", "file"],
                "level": log_level,
                "propagate": True,
            },
            "uvicorn": {
                "handlers": ["console"],
                "level": logging.INFO,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["console"],
                "level": logging.INFO,
                "propagate": False,
            },
        },
    }

    logging.config.dictConfig(LOGGING_CONFIG)
    
    # اختبار التسجيل
    logger = logging.getLogger(__name__)
    logger.info("Logging system initialized.")

# يتم استدعاء هذه الوظيفة في main.py
if __name__ == "__main__":
    setup_logging(logging.DEBUG)
    logger = logging.getLogger("test_logger")
    logger.debug("Test debug message")
    logger.info("Test info message")
    logger.warning("Test warning message")
    logger.error("Test error message")
