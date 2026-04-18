from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, Boolean, DateTime,
    ForeignKey, JSON, Text
)
from sqlalchemy.orm import relationship
from backend.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, default="default")
    level_score = Column(Float, default=5.0)  # i: 1-10
    created_at = Column(DateTime, default=datetime.utcnow)

    contents = relationship("Content", back_populates="user")
    user_cards = relationship("UserCard", back_populates="user")
    vocabularies = relationship("Vocabulary", back_populates="user")


class Content(Base):
    __tablename__ = "contents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, default="")
    source_type = Column(String)   # file | youtube
    source_path = Column(String)   # local path or YouTube URL
    audio_path = Column(String, default="")  # extracted WAV
    status = Column(String, default="processing")  # processing | ready | error
    error_msg = Column(Text, default="")
    steps_json = Column(JSON, default=list)   # [{name, label, status, message}]
    progress = Column(Integer, default=0)     # 0-100
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="contents")
    segments = relationship("Segment", back_populates="content")


class Segment(Base):
    __tablename__ = "segments"

    id = Column(Integer, primary_key=True, index=True)
    content_id = Column(Integer, ForeignKey("contents.id"), nullable=False)
    index = Column(Integer)
    text = Column(Text)
    start_time = Column(Float)
    end_time = Column(Float)
    audio_path = Column(String)

    # Difficulty scores (1-10 each)
    diff_speech_rate = Column(Float, default=5.0)
    diff_phonetics = Column(Float, default=5.0)
    diff_vocabulary = Column(Float, default=5.0)   # recalculated per user
    diff_complexity = Column(Float, default=5.0)
    diff_audio_quality = Column(Float, default=5.0)
    diff_total = Column(Float, default=5.0)         # max of all dimensions

    phonetic_annotations = Column(JSON, default=list)  # [{word, start, end, phenomena:[]}]
    word_timestamps = Column(JSON, default=list)        # WhisperX word-level data
    explanation = Column(Text, default="")              # Chinese linguistic explanation

    content = relationship("Content", back_populates="segments")
    user_cards = relationship("UserCard", back_populates="segment")


class UserCard(Base):
    __tablename__ = "user_cards"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    segment_id = Column(Integer, ForeignKey("segments.id"), nullable=False)

    # SRS state
    state = Column(String, default="new")   # new | learning | review | mastered
    interval_days = Column(Integer, default=1)
    ease_factor = Column(Float, default=2.5)
    next_review = Column(DateTime, nullable=True)
    last_reviewed = Column(DateTime, nullable=True)

    # Mastery gates
    shadow_streak = Column(Integer, default=0)    # need 3 consecutive passes
    gen_passed = Column(Boolean, default=False)
    stress_passed = Column(Boolean, default=False)

    # Stats
    total_attempts = Column(Integer, default=0)
    correct_attempts = Column(Integer, default=0)

    user = relationship("User", back_populates="user_cards")
    segment = relationship("Segment", back_populates="user_cards")


class Vocabulary(Base):
    __tablename__ = "vocabulary"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    word = Column(String, index=True)

    # Bayesian mastery probability
    mastery_prob = Column(Float, default=0.0)   # 0.0 - 1.0
    # Color state derived from mastery_prob:
    #   < 0.30  → blue  (unknown)
    #   < 0.85  → yellow (learning)
    #   >= 0.85 → white  (mastered)

    encounters = Column(Integer, default=0)
    correct_count = Column(Integer, default=0)
    last_seen = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="vocabularies")
