import os
import pytest
from app.core.user_manager import UserManager

# Create a temporary DB file for testing
TEST_DB = "test_users.json"

@pytest.fixture
def user_manager():
    # Setup
    um = UserManager()
    um.db_path = TEST_DB
    um._save_db({}) # Clear db
    yield um
    # Teardown
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

def test_create_and_auth_user(user_manager):
    # 1. Create User
    assert user_manager.create_user("testuser", "secret123", role="student") == True
    
    # 2. Try duplicate
    assert user_manager.create_user("testuser", "newpass") == False
    
    # 3. Authenticate Success
    user = user_manager.authenticate("testuser", "secret123")
    assert user is not None
    assert user['username'] == "testuser"
    assert "password" not in user # Ensure security check works

    # 4. Authenticate Fail
    assert user_manager.authenticate("testuser", "wrongpass") is None