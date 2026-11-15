"""Monthly trial balances for Accounting & Bookkeeping engagements.

One MonthlyTB row per upload (a month's trial balance, or the opening balances); its lines hold one amount per account (debit positive,
credit negative). monthly_tb.py consolidates the uploads cumulatively and reconciles the result to the year-end Trial Balance."""
from datetime import datetime

from extensions import db

MONTHLY_TB_BASES = [("movement", "The month's movements (transactions in the month only)"),
                    ("closing", "Closing balances at month-end (year to date for income and expenses)"),
                    ("opening", "Opening balances at the start of the year")]


class MonthlyTB(db.Model):
    __tablename__ = "monthly_tb"
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey("engagement.id"), nullable=False, index=True)
    period = db.Column(db.String(7), nullable=False)       # "YYYY-MM" - the month this trial balance is for
    basis = db.Column(db.String(10), nullable=False, default="movement")   # movement | closing | opening
    original_filename = db.Column(db.String(255))
    note = db.Column(db.String(300))
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    engagement = db.relationship("Engagement", backref=db.backref("monthly_tbs", lazy=True, cascade="all, delete-orphan", order_by="MonthlyTB.period, MonthlyTB.id"))
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    lines = db.relationship("MonthlyTBLine", backref="monthly_tb", cascade="all, delete-orphan", order_by="MonthlyTBLine.id")


class MonthlyTBLine(db.Model):
    __tablename__ = "monthly_tb_line"
    id = db.Column(db.Integer, primary_key=True)
    monthly_tb_id = db.Column(db.Integer, db.ForeignKey("monthly_tb.id"), nullable=False, index=True)
    account_code = db.Column(db.String(50))
    account_name = db.Column(db.String(200), nullable=False)
    net_dr = db.Column(db.Float, default=0.0)               # debit positive, credit negative
