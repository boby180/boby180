"""עיצוב. צבעים מפורשים רק לרכיבים שלנו, השאר לפי ערכת הנושא של המערכת."""

STYLE = """
QWidget { font-size: 11pt; }
#card {
    background: #ffffff; color: #1f2933;
    border: 1px solid #d5dbe1; border-radius: 14px;
}
#card QLabel { color: #1f2933; background: transparent; }
#front { font-size: 44pt; }
#back1 { font-size: 24pt; color: #1d4f91; }
#back2 { font-size: 18pt; color: #52606d; }
#counts { font-size: 11pt; }
#doneMsg { font-size: 16pt; }
QPushButton { padding: 7px 16px; }
#show { font-size: 13pt; padding: 12px; }
QPushButton[grade] {
    color: white; border: none; border-radius: 8px;
    font-size: 12pt; padding: 10px 6px; min-width: 110px;
}
QPushButton[grade="1"] { background: #c0392b; }
QPushButton[grade="2"] { background: #b9770e; }
QPushButton[grade="3"] { background: #1e8449; }
QPushButton[grade="4"] { background: #21618c; }
QPushButton[grade]:hover { border: 2px solid rgba(255, 255, 255, 0.7); }
"""
