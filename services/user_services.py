from __future__ import annotations
from sqlalchemy.orm import Session
from sqlalchemy import or_
from models import User, UserTask
from handlers.funcs import exception_decorator
from typing import Literal, Generator

class UserService:        
    @staticmethod
    @exception_decorator
    def get_user(db: Session, username: str = None, user_tID: str = None, user_ID: int = None) -> User | None:
        """
        Retrieve a user by one or more identifiers:
        - username
        - Telegram ID (user_tID)
        - internal database ID (user_ID)

        Returns the first match or None if not found.
        """
        if user_tID:
            user_tID = str(user_tID)

        if user_ID:
            return db.query(User).filter(User.id == user_ID).first()
        elif username is not None and user_tID is not None:
            return db.query(User).filter(User.telegram_id == user_tID, User.username == username).first()
        elif username is not None:
            return db.query(User).filter(User.username == username).first()
        elif user_tID is not None:
            return db.query(User).filter(User.telegram_id == user_tID).first()
        else:
            return None
    
    @staticmethod
    @exception_decorator
    def get_or_create_user(db: Session, username: str, telegram_id: int = None, is_admin: bool = False) -> User | None:
        """
        Retrieve a user by username or Telegram ID.
        If the user does not exist, create a new one.
        Updates Telegram ID if user already exists. Does not demote existing admins.
        """
        if not username:
            return None
        user = None

        # Prefer lookup by telegram_id if provided
        if telegram_id:
            user = db.query(User).filter(User.telegram_id == str(telegram_id)).first()
        # Fallback to username lookup
        if user is None:
            user = db.query(User).filter(User.username == username).first()

        if not user:
            user = User(username=username, is_admin=is_admin)
            db.add(user)

        if telegram_id:
            user.telegram_id = str(telegram_id)

        # Only promote, never demote existing admin status here
        user.is_admin = user.is_admin or is_admin
        
        db.commit()
        db.refresh(user)
        return user
    
    @staticmethod
    @exception_decorator
    def assign_user_to_task(db: Session, user_ID: str, task_id: int) -> True | None:
        """
        Assign a user to a task.
        Checks if the assignment already exists to avoid duplicates.
        """
        existing_assignment = db.query(UserTask).filter(
            UserTask.user_id == user_ID,
            UserTask.task_id == task_id
        ).first()
        
        if not existing_assignment:
            user_task = UserTask(user_id=user_ID, task_id=task_id)
            db.add(user_task)
            db.commit()
        
        return True
    
    @staticmethod
    @exception_decorator
    def is_admin(db: Session, user_tID: str = None, username: str = None) -> bool | None:
        """
        Check if a user is an admin.
        Returns True, False, or None if the user does not exist.
        """
        user = UserService.get_user(db=db, username=username, user_tID=user_tID)
        if not user:
            return None
        
        return user.is_admin
    
    @staticmethod
    @exception_decorator
    def del_user(db: Session, username: str = None, user_ID: int = None) -> Literal[True, "NOT_EXIST"] | None:
        """
        Delete a user by username or internal ID.
        Returns True if deleted, "NOT_EXIST" if the user was not found.
        """
        user = UserService.get_user(db=db, username=username, user_ID=user_ID)
        if not user:
            return "NOT_EXIST"
        
        db.delete(user)
        db.commit()

        return True

    @staticmethod
    @exception_decorator
    def get_all_users(db: Session, user_tID: str = None, username: str = None, task_id: int = None) -> Generator[User, None, None]:
        """
        Retrieve all users optionally filtered by:
        - Exclude the user with given Telegram ID
        - Exclude the user with given username
        - Exclude users already assigned to a specific task
        Returns a generator of User objects.
        """
        if user_tID:
            user_tID = str(user_tID)
            query = db.query(User).filter(
                or_(
                    User.telegram_id != user_tID,
                    User.telegram_id.is_(None)
                )
            )
            if task_id:
                # Exclude users already assigned to the task
                subq = db.query(UserTask.user_id).filter(UserTask.task_id == task_id)
                query = query.filter(User.id.notin_(subq))
                
        elif username:
            query = db.query(User).filter(
                or_(
                    User.username != username,
                    User.username.is_(None)
                )
            )
        else:
            query = db.query(User)

        yield from query 

    @staticmethod
    @exception_decorator
    def toggle_user(db: Session, user_ID: int = None) -> True | None:
        """
        Toggle the admin status of a user.
        If user is admin, remove admin; if not, grant admin.
        """
        user = UserService.get_user(db=db, user_ID=user_ID)
        if not user:
            return None
        
        user.is_admin = not user.is_admin

        db.commit()
        db.refresh(user)

        return True
