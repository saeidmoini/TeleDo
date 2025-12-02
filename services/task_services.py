from __future__ import annotations
from sqlalchemy.orm import Session
from models import Group, Topic, User, Task, UserTask, TaskAttachment
from datetime import datetime
from logger import logger
from typing import List, Literal
from handlers.funcs import exception_decorator
from utils.date_utils import jalali_to_gregorian

class TaskService:
    VALID_STATUSES = {"pending", "in_progress", "done", "blocked"}

    @staticmethod
    @exception_decorator
    def get_or_create_group(db: Session, telegram_group_id: str, name: str = None) -> Group | None:
        """
        Retrieve a group by its telegram_group_id, or create a new one if it does not exist.
        """
        if not telegram_group_id:
            return None
        telegram_group_id = str(telegram_group_id)
        
        # Query for an existing group
        group = db.query(Group).filter(Group.telegram_id == telegram_group_id).first()
        
        if not group:
            # Create new group if it does not exist
            group = Group(telegram_id=telegram_group_id, name=name)
            db.add(group)
            db.commit()
            db.refresh(group)
        return group
    
    @staticmethod
    @exception_decorator
    def get_group(db: Session, id: int = None, tID: str = None) -> Group | None:
        """
        Retrieve a group either by its database ID or Telegram ID.
        """
        if id==None and tID==None:
            return None
        if tID:
            group = db.query(Group).filter(Group.telegram_id == tID).first()
        else:
            group = db.query(Group).filter(Group.id == id).first()
        return group
    
    @staticmethod
    @exception_decorator
    def get_topic(db: Session, id: int = None, tID: int = None) -> Topic | None:
        """
        Retrieve a topic by its database ID.
        """
        if tID:
            topic = db.query(Topic).filter(Topic.telegram_id == tID).first()
            return topic
        if id==None:
            return None
        topic = db.query(Topic).filter(Topic.id == id).first()
        return topic

    @staticmethod
    @exception_decorator
    def get_or_create_topic(db: Session, telegram_topic_id: str, group_id: int, name: str, link: str):
        """
        Retrieve a topic by its telegram_topic_id and group_id,
        or create a new one if it does not exist.
        """
        if not telegram_topic_id:
            return None
        telegram_topic_id = str(telegram_topic_id)
            
        # Query for an existing topic
        topic = db.query(Topic).filter(
            Topic.telegram_id == telegram_topic_id, 
            Topic.group_id == group_id
        ).first()
        
        if not topic:
            # Create new topic if it does not exist
            topic = Topic(telegram_id=telegram_topic_id, group_id=group_id, name=name, link=link)
            db.add(topic)
            db.commit()
            db.refresh(topic)
        return topic

    @staticmethod
    @exception_decorator
    def create_task(
        db: Session,
        title: str,
        group_id: int = None,
        topic_id: int = None,
        admin_id: int = None,
        description: str = None,
        end_date = None
    ) -> Task | None:
        """
        Create a new task with optional group, topic, admin, title, description, and end_date.
        If end_date is a string in 'YYYY-MM-DD' format, convert it to datetime.
        """
        
        # Convert end_date string to datetime object if necessary
        end_date_obj = None
        if end_date:
            if isinstance(end_date, str):
                end_date_obj = jalali_to_gregorian(end_date)
            elif isinstance(end_date, datetime):
                end_date_obj = end_date
        
        # Create the task object
        task = Task(
            group_id=group_id,
            topic_id=topic_id,
            admin_id=admin_id,
            title=title,
            description=description,
            end_date=end_date_obj
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
        Retrieve a single task by its database ID.
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
    def edit_task(db: Session, task_id: int, name: str = None, description: str = None, start_date: str = None, end_date: str = None, status: str = None) -> Literal[True, "NOT_EXIST"] | None:
        """
        Edit task details such as name, description, start_date, end_date, and status.
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
            if isinstance(start_date, str):
                task.start_date = jalali_to_gregorian(start_date)
            else:
                task.start_date = start_date
        if end_date:
            if isinstance(end_date, str):
                task.end_date = jalali_to_gregorian(end_date)
            else:
                task.end_date = end_date
        if status and status in TaskService.VALID_STATUSES:
            task.status = status

        db.commit()
        db.refresh(task)
        return True

    @staticmethod
    @exception_decorator
    def get_all_groups(db: Session) -> List[Group] | None:
        """
        Retrieve all groups from the database.
        """
        groups = db.query(Group).all()
        return groups
    
    @staticmethod
    @exception_decorator
    def get_all_topics(db: Session, group_id: int = None) -> List[Topic] | None:
        """
        Retrieve all topics from the database.
        """
        if group_id:
            topics = db.query(Topic).filter(Topic.group_id == group_id).all()
            return topics
        topics = db.query(Topic).all()
        return topics
    
    @staticmethod
    @exception_decorator
    def get_all_tasks(db: Session, group_id: int = None, topic_id: int = None) -> List[Task] | None:
        """
        Retrieve all tasks.
        It can be filltered by group_id or topic_id
        """
        if group_id != None and topic_id == False:
            tasks = db.query(Task).filter(Task.group_id==group_id, Task.topic_id.is_(None)).all()
        elif group_id:
            tasks = db.query(Task).filter(Task.group_id == group_id).all()
        elif group_id == False:
            tasks = db.query(Task).filter(Task.group_id.is_(None)).all()
        elif topic_id:
            tasks = db.query(Task).filter(Task.topic_id == topic_id).all()
        elif topic_id == False:
            tasks = db.query(Task).filter(Task.topic_id.is_(None)).all() 
        else:
            tasks = db.query(Task).all()
        return tasks

    @staticmethod
    @exception_decorator
    def get_tasks_for_user(db: Session, user_id: int) -> List[Task]:
        """
        Retrieve all tasks assigned to a specific user.
        """
        tasks = db.query(Task).join(UserTask).filter(
            UserTask.user_id == user_id
        ).all()
        return tasks
    
    @staticmethod
    @exception_decorator
    def is_user_assigned(db: Session, task_id: int, user_id: int) -> bool:
        """Check if a user is assigned to a given task."""
        exists = db.query(UserTask).filter(
            UserTask.task_id == task_id,
            UserTask.user_id == user_id
        ).first()
        return bool(exists)

    @staticmethod
    @exception_decorator
    def update_status(db: Session, task_id: int, status: str) -> Literal[True, "NOT_EXIST"] | None:
        """Update task status if valid."""
        if status not in TaskService.VALID_STATUSES:
            return None
        return TaskService.edit_task(db=db, task_id=task_id, status=status)

class TaskAttachmentService:
    
    @staticmethod
    @exception_decorator
    def get_attachments(db: Session, task_id: int) -> List[int]:
        """Get all attachment IDs for a task"""
        record = db.query(TaskAttachment).filter(TaskAttachment.task_id == task_id).first()
        if record:
            return record.attachment_ids
        return []

    @staticmethod
    @exception_decorator
    def add_attachment(db: Session, task_id: int, attachment_id: str) -> bool:
        """Add a new attachment ID to a task. Returns True if added, False if duplicate."""
        record = db.query(TaskAttachment).filter(TaskAttachment.task_id == task_id).first()
        if record:
            # Append if not exists
            if attachment_id not in record.attachment_ids:
                record.attachment_ids.append(attachment_id)
            else:
                return False
        else:
            record = TaskAttachment(task_id=task_id, attachment_ids=[attachment_id])
            db.add(record)
        db.commit()
        return True




