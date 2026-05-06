"""
Shared extension instances. Importing from here avoids circular imports
between the app factory and the model modules.
"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
