from fastapi.testclient import TestClient
from app.core import auth
from app.main import app

client = TestClient(app)


def _login_as(username: str, role: str = "student"):
    """Give the test client a valid session cookie.

    Per-student endpoints now require one. This test used to pass BECAUSE
    /api/v1/assignments/student/{username} was reachable unauthenticated — the
    exact hole a security reviewer used to read other students' data.
    """
    client.cookies.set(auth.COOKIE_NAME, auth.create_token(username, role))


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_assignment_flow():
    # 1. A TEACHER creates the assignment. Creation is teacher-only now; this call
    #    used to succeed with no session at all.
    _login_as("teacher1", "teacher")
    payload = {
        "teacher": "teacher1",
        "student": "student1",
        "question": "What is 2+2?"
    }
    response = client.post("/api/v1/assignments/create", json=payload)
    assert response.status_code == 200
    aid = response.json()["id"]

    # 2. Student fetches it — as themselves.
    _login_as("student1")
    res_get = client.get("/api/v1/assignments/student/student1")
    assert res_get.status_code == 200
    body = res_get.json()
    assert len(body) > 0
    assert any(a["id"] == aid for a in body)


def test_assignments_require_a_session():
    """No cookie → no data. This is the reported vulnerability, as a test."""
    client.cookies.clear()
    res = client.get("/api/v1/assignments/student/student1")
    assert res.status_code == 401


def test_student_cannot_create_assignments():
    """Assignment creation is a teacher action."""
    _login_as("student1")
    res = client.post("/api/v1/assignments/create",
                      json={"teacher": "student1", "student": "student2",
                            "question": "pwn"})
    assert res.status_code == 403
    client.cookies.clear()


def test_student_cannot_read_another_students_assignments():
    _login_as("student1")
    res = client.get("/api/v1/assignments/student/student2")
    assert res.status_code == 403
    client.cookies.clear()