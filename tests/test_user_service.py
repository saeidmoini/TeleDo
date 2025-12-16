import pytest

from models import User
from services.user_services import UserService


def test_get_or_create_user_creates_and_updates(db_session):
    user = UserService.get_or_create_user(db_session, username="alice", telegram_id=111, is_admin=True)
    assert user is not None
    assert user.username == "alice"
    assert user.telegram_id == "111"
    assert user.is_admin is True

    # Second call should not demote admin and should update telegram_id when provided.
    user2 = UserService.get_or_create_user(db_session, username="alice", telegram_id=222, is_admin=False)
    assert user2.id == user.id
    assert user2.telegram_id == "222"
    assert user2.is_admin is True


def test_is_admin_and_toggle(db_session):
    user = User(username="bob", telegram_id="333", is_admin=False)
    db_session.add(user)
    db_session.commit()

    assert UserService.is_admin(db_session, username="bob") is False
    UserService.toggle_user(db_session, user_ID=user.id)
    assert UserService.is_admin(db_session, username="bob") is True
    UserService.toggle_user(db_session, user_ID=user.id)
    assert UserService.is_admin(db_session, username="bob") is False


def test_del_user(db_session):
    user = User(username="charlie")
    db_session.add(user)
    db_session.commit()

    assert UserService.del_user(db_session, user_ID=user.id) is True
    assert UserService.del_user(db_session, user_ID=user.id) == "NOT_EXIST"


def test_get_all_users_filters(db_session):
    u1 = User(username="alice", telegram_id="1")
    u2 = User(username="bob", telegram_id="2")
    u3 = User(username="carol", telegram_id=None)
    db_session.add_all([u1, u2, u3])
    db_session.commit()

    # Exclude by telegram_id
    result = list(UserService.get_all_users(db_session, user_tID="1"))
    assert u1 not in result
    assert u2 in result and u3 in result

    # Exclude by username
    result = list(UserService.get_all_users(db_session, username="bob"))
    assert u2 not in result
    assert u1 in result and u3 in result


def test_get_or_create_updates_username_on_change(db_session):
    existing = User(username="oldname", telegram_id="99", is_admin=False)
    db_session.add(existing)
    db_session.commit()

    updated = UserService.get_or_create_user(db_session, username="newname", telegram_id=99, is_admin=False)

    assert updated.id == existing.id
    assert updated.username == "newname"
    assert updated.telegram_id == "99"


def test_get_user_syncs_username_with_profile(db_session):
    user = User(username="stale_name", telegram_id="100")
    db_session.add(user)
    db_session.commit()

    fetched = UserService.get_user(db_session, user_tID="100", username="fresh_name")

    assert fetched.username == "fresh_name"
