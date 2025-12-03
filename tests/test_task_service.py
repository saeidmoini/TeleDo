from datetime import datetime

import pytest

from models import Group, Task, Topic, User, UserTask
from services.task_services import TaskAttachmentService, TaskService


def test_create_and_edit_task_with_jalali_date(db_session):
    admin = User(username="admin", is_admin=True)
    db_session.add(admin)
    db_session.commit()

    task = TaskService.create_task(
        db_session,
        title="Test Task",
        admin_id=admin.id,
        description="desc",
        end_date="1403-10-10",  # jalali string
    )
    assert isinstance(task.end_date, datetime)
    assert task.title == "Test Task"

    TaskService.edit_task(
        db_session,
        task_id=task.id,
        name="New Title",
        description="new desc",
        status="done",
    )
    updated = TaskService.get_task_by_id(db_session, task.id)
    assert updated.title == "New Title"
    assert updated.description == "new desc"
    assert updated.status == "done"


def test_update_status_validation(db_session):
    task = Task(title="X", admin_id=1)
    db_session.add(task)
    db_session.commit()

    assert TaskService.update_status(db_session, task_id=task.id, status="invalid") is None
    assert TaskService.update_status(db_session, task_id=task.id, status="pending") is True


def test_group_topic_helpers(db_session):
    group = TaskService.get_or_create_group(db_session, telegram_group_id="100", name="Group A")
    assert group is not None
    same_group = TaskService.get_or_create_group(db_session, telegram_group_id="100", name="Group A")
    assert group.id == same_group.id

    topic = TaskService.get_or_create_topic(db_session, telegram_topic_id="200", group_id=group.id, name="Topic", link="link")
    assert topic is not None
    fetched_topic = TaskService.get_topic(db_session, id=topic.id)
    assert fetched_topic.id == topic.id


def test_user_assignment_and_checks(db_session):
    user = User(username="user1")
    task = Task(title="task", admin_id=1)
    db_session.add_all([user, task])
    db_session.commit()

    assignment = UserTask(user_id=user.id, task_id=task.id)
    db_session.add(assignment)
    db_session.commit()

    users = TaskService.get_task_users(db_session, task_id=task.id)
    assert user in users
    assert TaskService.is_user_assigned(db_session, task_id=task.id, user_id=user.id) is True

    assert TaskService.delete_user_from_task(db_session, task_id=task.id, user_id=999) == "NOT_EXIST"
    assert TaskService.delete_user_from_task(db_session, task_id=task.id, user_id=user.id) is True
    assert TaskService.is_user_assigned(db_session, task_id=task.id, user_id=user.id) is False


def test_get_all_tasks_filters(db_session):
    g1 = Group(telegram_id="g1", name="G1")
    g2 = Group(telegram_id="g2", name="G2")
    t1 = Topic(telegram_id="t1", group=g1, name="T1")
    task1 = Task(title="t1", admin_id=1, group=g1)
    task2 = Task(title="t2", admin_id=1, group=g2, topic=t1)
    task3 = Task(title="t3", admin_id=1, topic=t1)
    db_session.add_all([g1, g2, t1, task1, task2, task3])
    db_session.commit()

    assert task1 in TaskService.get_all_tasks(db_session, group_id=g1.id)
    assert task2 in TaskService.get_all_tasks(db_session, group_id=g2.id)
    # topic-specific
    topic_tasks = TaskService.get_all_tasks(db_session, topic_id=t1.id)
    assert task2 in topic_tasks and task3 in topic_tasks


def test_attachments(db_session):
    task = Task(title="attach", admin_id=1)
    db_session.add(task)
    db_session.commit()

    assert TaskAttachmentService.get_attachments(db_session, task_id=task.id) == []
    assert TaskAttachmentService.add_attachment(db_session, task_id=task.id, attachment_id="file1") is True
    # Duplicate attachment should return False
    assert TaskAttachmentService.add_attachment(db_session, task_id=task.id, attachment_id="file1") is False
    assert TaskAttachmentService.add_attachment(db_session, task_id=task.id, attachment_id="file2") is True

    attachments = TaskAttachmentService.get_attachments(db_session, task_id=task.id)
    assert attachments == ["file1", "file2"]
