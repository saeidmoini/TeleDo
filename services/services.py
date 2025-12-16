from __future__ import annotations
from sqlalchemy.orm import Session
from models import Group, Topic, User, Task, UserTask
from datetime import datetime
from logger import logger
from typing import List, Literal
from utils.decorators import exception_decorator

class TaskService:
    @staticmethod
    @exception_decorator
    def get_or_create_group(db: Session, telegram_group_id: str):
        """
        Retrieve a group by its telegram_group_id, or create a new one if it does not exist.
        """
        group = db.query(Group).filter(Group.telegram_id == telegram_group_id).first()
        if not group:
            group = Group(telegram_id=telegram_group_id)
            db.add(group)
            db.commit()
            db.refresh(group)
        return group

    @staticmethod
    @exception_decorator
    def get_or_create_topic(db: Session, telegram_topic_id: str, group_id: int):
        """
        Retrieve a topic by its telegram_topic_id and group_id, or create a new one if it does not exist.
        """
        if not telegram_topic_id:
            return None
            
        topic = db.query(Topic).filter(
            Topic.telegram_id == telegram_topic_id, 
            Topic.group_id == group_id
        ).first()
        
        if not topic:
            topic = Topic(telegram_id=telegram_topic_id, group_id=group_id)
            db.add(topic)
            db.commit()
            db.refresh(topic)
        return topic

    @staticmethod
    @exception_decorator
    def create_task(
        db: Session,
        group_id: int = None,
        topic_id: int = None,
        admin_id: int = None,
        title: str = None,
        description: str = None,
        start_date = None,
        end_date = None
    ):
        """
        Create a new task with optional group, topic, admin, title, description, and dates.
        Dates can be strings in 'YYYY-MM-DD' format or datetime objects.
        """
        # Convert start_date string to datetime object
        start_date_obj = None
        if start_date and isinstance(start_date, str):
            try:
                start_date_obj = datetime.strptime(start_date, '%Y-%m-%d')
            except ValueError:
                start_date_obj = None
        
        # Convert end_date string to datetime object
        end_date_obj = None
        if end_date and isinstance(end_date, str):
            try:
                end_date_obj = datetime.strptime(end_date, '%Y-%m-%d')
            except ValueError:
                end_date_obj = None
        
        task = Task(
            group_id=group_id,
            topic_id=topic_id,
            admin_id=admin_id,
            title=title,
            description=description,
            start_date=start_date_obj,
            end_date=end_date_obj,
            status="pending"
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task
    
    @staticmethod
    @exception_decorator
    def get_task_by_admin_id(db: Session, admin_id: int) -> List[Task] | None:
        """
        Retrieve all tasks created by a specific admin.
        """
        tasks = db.query(Task).filter(
            Task.admin_id == admin_id
        ).all()
        return tasks
    
    @staticmethod
    @exception_decorator
    def get_task_by_id(db: Session, id: int) -> Task | None:
        """
        Retrieve a single task by its ID.
        """
        task = db.query(Task).filter(
            Task.id == id
        ).first()
        return task
    
    @staticmethod
    @exception_decorator
    def delete_task(db: Session, task: Task) -> True | None:
        """
        Delete a task from the database.
        """
        db.delete(task)
        db.commit()
        return True

    @staticmethod
    @exception_decorator
    def get_task_users(db: Session, task_id: int) -> List[User] | None:
        """
        Retrieve all users assigned to a specific task.
        """
        assigned_users = db.query(User).join(UserTask).filter(
            UserTask.task_id == task_id
        ).all()
        return assigned_users

    @staticmethod
    @exception_decorator
    def delete_user_from_task(db: Session, task_id: int, user_id: int) -> Literal[True, "NOT_EXIST"] | None:
        """
        Remove a user assignment from a task.
        Returns "NOT_EXIST" if the user-task relation does not exist.
        """
        user_task_assignment = db.query(UserTask).filter(
            UserTask.user_id == user_id,
            UserTask.task_id == task_id
        ).first()

        if not user_task_assignment:
            return "NOT_EXIST"

        db.delete(user_task_assignment)
        db.commit()
        return True

    @staticmethod
    @exception_decorator
    def edit_task(db: Session, task_id: int, name: str = None, description: str = None, start_date: str = None, end_date: str = None) -> Literal[True, "NOT_EXIST"] | None:
        """
        Edit task details such as name, description, start_date, and end_date.
        Returns "NOT_EXIST" if the task does not exist.
        """
        task = db.query(Task).filter(
            Task.id == task_id
        ).first()

        if not task:
            return "NOT_EXIST"

        if name:
            task.title = name
        if description:
            task.description = description
        if start_date:
            task.start_date = start_date
        if end_date:
            task.end_date = end_date

        db.commit()
        db.refresh(task)


class UserService:        
    @staticmethod
    @exception_decorator
    def get_user(db: Session, username: str = None, user_tID: int = None) -> User | None:
        """
        Retrieve a user by username, user_tID, or both.
        """
        if username is not None and user_tID is not None:
            return db.query(User).filter(User.telegram_id == user_tID, User.username == username).first()
        elif username is not None:
            return db.query(User).filter(User.username == username).first()
        elif user_tID is not None:
            return db.query(User).filter(User.telegram_id == user_tID).first()
        else:
            return None
    
    @staticmethod
    @exception_decorator
    def get_or_create_user(db: Session, username: str, telegram_id: int, is_admin: bool = False) -> User | None:
        """
        Retrieve a user by username, or create one if it does not exist.
        Updates admin status and group_id if user already exists.
        """
        if not username or telegram_id:
            return None
            
        user = db.query(User).filter(User.username == username).first()
        if not user:
            user = User(username=username, is_admin=is_admin, telegram_id=telegram_id)
            db.add(user)
        else:
            user.is_admin = is_admin
            user.telegram_id = telegram_id
        
        db.commit()
        db.refresh(user)
        return user
    
    @staticmethod
    @exception_decorator
    def assign_user_to_task(db: Session, user_id: int, task_id: int) -> True | None:
        """
        Assign a user to a task if the assignment does not already exist.
        """
        existing_assignment = db.query(UserTask).filter(
            UserTask.user_id == user_id,
            UserTask.task_id == task_id
        ).first()
        
        if not existing_assignment:
            user_task = UserTask(user_id=user_id, task_id=task_id)
            db.add(user_task)
            db.commit()
        
        return True
