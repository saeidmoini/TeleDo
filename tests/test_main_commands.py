import pytest
from aiogram.types import BotCommandScopeChat, BotCommandScopeDefault

import main
from models import User


class FakeBot:
    def __init__(self):
        self.calls = []

    async def set_my_commands(self, commands, scope=None):
        self.calls.append((commands, scope))


@pytest.mark.asyncio
async def test_set_commands_scopes(db_session, monkeypatch):
    admin = User(username="admin", telegram_id="555", is_admin=True)
    db_session.add(admin)
    db_session.commit()

    def fake_get_db():
        yield db_session

    monkeypatch.setattr(main, "get_db", fake_get_db)

    bot = FakeBot()
    await main.set_commands(bot)

    assert len(bot.calls) == 2

    user_commands, user_scope = bot.calls[0]
    assert isinstance(user_scope, BotCommandScopeDefault)
    assert len(user_commands) == 1
    assert user_commands[0].command == "/tasks"

    admin_commands, admin_scope = bot.calls[1]
    assert isinstance(admin_scope, BotCommandScopeChat)
    assert admin_scope.chat_id == 555
    assert {cmd.command for cmd in admin_commands} == {"/tasks", "/users"}
