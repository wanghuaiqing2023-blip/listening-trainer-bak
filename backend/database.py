from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from backend.config import settings

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations() -> None:
    """Add columns that may be missing in pre-existing databases."""
    migrations = [
        # (table, column, column_def)
        ("contents", "steps_json",  "TEXT DEFAULT '[]'"),
        ("contents", "progress",    "INTEGER DEFAULT 0"),
        ("contents", "error_msg",   "TEXT DEFAULT ''"),
        ("contents", "audio_path",  "TEXT DEFAULT ''"),
        ("segments", "explanation", "TEXT DEFAULT ''"),
    ]
    with engine.connect() as conn:
        for table, column, col_def in migrations:
            # Check if column already exists
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            existing = {row[1] for row in rows}
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"))
                conn.commit()
