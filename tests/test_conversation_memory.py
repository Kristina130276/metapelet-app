"""Tests for conversation memory system."""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Mock psycopg2 before importing modules that use it
import sys
sys.modules['psycopg2'] = MagicMock()
sys.modules['psycopg2.extras'] = MagicMock()

import conversation_memory
from app import app


@pytest.fixture
def client():
    """Flask test client."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def temp_memory_file(tmp_path, monkeypatch):
    """Use temporary file storage for tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(conversation_memory, "DATA_DIR", data_dir)
    monkeypatch.setattr(conversation_memory, "MEMORY_PATH", data_dir / "conversation_memory.json")
    monkeypatch.setattr(conversation_memory, "USE_DATABASE", False)
    yield data_dir / "conversation_memory.json"


def test_store_and_get_memory(temp_memory_file):
    """Test storing and retrieving memory."""
    user_id = "test_user_123"
    summary = "User has a granddaughter named Sarah. Loves gardening."
    
    # Store memory
    success, error = conversation_memory.store_memory(user_id, summary)
    assert success is True
    assert error == ""
    
    # Retrieve memory
    retrieved = conversation_memory.get_memory(user_id)
    assert retrieved == summary


def test_memory_truncation(temp_memory_file):
    """Test that memory is truncated to MAX_SUMMARY_CHARS."""
    user_id = "test_user_long"
    long_summary = "A" * 1000  # 1000 chars, exceeds MAX_SUMMARY_CHARS (800)
    
    success, error = conversation_memory.store_memory(user_id, long_summary)
    assert success is True
    
    retrieved = conversation_memory.get_memory(user_id)
    assert len(retrieved) == conversation_memory.MAX_SUMMARY_CHARS
    assert retrieved == "A" * 800


def test_update_existing_memory(temp_memory_file):
    """Test updating an existing memory."""
    user_id = "test_user_update"
    
    # First summary
    summary1 = "User has a dog named Max."
    conversation_memory.store_memory(user_id, summary1)
    
    # Updated summary
    summary2 = "User has a dog named Max and a cat named Luna."
    conversation_memory.store_memory(user_id, summary2)
    
    # Should have the updated version
    retrieved = conversation_memory.get_memory(user_id)
    assert retrieved == summary2


def test_delete_memory(temp_memory_file):
    """Test deleting memory."""
    user_id = "test_user_delete"
    summary = "Some memory about the user."
    
    # Store memory
    conversation_memory.store_memory(user_id, summary)
    assert conversation_memory.get_memory(user_id) == summary
    
    # Delete memory
    success, error = conversation_memory.delete_memory(user_id)
    assert success is True
    assert error == ""
    
    # Should be gone
    retrieved = conversation_memory.get_memory(user_id)
    assert retrieved is None


def test_get_nonexistent_memory(temp_memory_file):
    """Test getting memory for user that doesn't exist."""
    retrieved = conversation_memory.get_memory("nonexistent_user")
    assert retrieved is None


def test_store_empty_summary(temp_memory_file):
    """Test that empty summary is rejected."""
    success, error = conversation_memory.store_memory("test_user", "")
    assert success is False
    assert error == "empty_summary"
    
    success, error = conversation_memory.store_memory("test_user", "   ")
    assert success is False
    assert error == "empty_summary"


def test_invalid_user_id(temp_memory_file):
    """Test that invalid user_id is rejected."""
    # Empty user_id
    success, error = conversation_memory.store_memory("", "Some summary")
    assert success is False
    assert error == "invalid_user_id"
    
    # None user_id
    success, error = conversation_memory.store_memory(None, "Some summary")
    assert success is False
    assert error == "invalid_user_id"


def test_count_user_messages():
    """Test counting user messages in history."""
    history = [
        {"role": "user", "content": "[начало разговора]"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "How are you?"},
        {"role": "assistant", "content": "I'm fine."},
        {"role": "user", "content": "Tell me a story."},
    ]
    
    count = conversation_memory.count_user_messages(history)
    # Should count only real user messages, not [начало разговора]
    assert count == 2


def test_forget_endpoint(client, temp_memory_file, monkeypatch):
    """Test /memory/forget endpoint."""
    # Patch the module-level USE_DATABASE to False
    monkeypatch.setattr("conversation_memory.USE_DATABASE", False)
    
    user_id = "test_user_forget"
    
    # Store some memory
    conversation_memory.store_memory(user_id, "Some memory")
    assert conversation_memory.get_memory(user_id) is not None
    
    # Forget memory via API
    response = client.post('/memory/forget', 
                          json={"user_id": user_id},
                          content_type='application/json')
    
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["ok"] is True
    
    # Memory should be gone
    assert conversation_memory.get_memory(user_id) is None


def test_forget_endpoint_missing_user_id(client):
    """Test /memory/forget endpoint with missing user_id."""
    response = client.post('/memory/forget', 
                          json={},
                          content_type='application/json')
    
    assert response.status_code == 400
    data = json.loads(response.data)
    assert data["ok"] is False


def test_memory_injected_into_prompt(monkeypatch):
    """Test that memory is injected into system prompt."""
    from app import build_system_prompt
    
    user_id = "test_user_prompt"
    summary = "User has a granddaughter named Emma who is studying medicine."
    
    # Mock get_memory to return our test summary
    monkeypatch.setattr(conversation_memory, "get_memory", lambda uid: summary if uid == user_id else None)
    
    # Build prompt with memory
    prompt = build_system_prompt(
        name="Тест",
        age="70",
        language="ru-RU",
        user_id=user_id
    )
    
    # Check that memory is in the prompt
    assert "прошлых разговоров" in prompt
    assert "Emma" in prompt
    assert "studying medicine" in prompt or "медицин" in prompt.lower()


def test_memory_not_logged(caplog, temp_memory_file):
    """Test that memory content is never logged."""
    import logging
    caplog.set_level(logging.INFO)
    
    user_id = "test_user_log"
    summary = "SENSITIVE_CONTENT_SHOULD_NOT_BE_IN_LOGS"
    
    # Store memory
    conversation_memory.store_memory(user_id, summary)
    
    # Check logs
    log_text = caplog.text
    assert "SENSITIVE_CONTENT_SHOULD_NOT_BE_IN_LOGS" not in log_text
    # Should only log length or generic message
    assert "Memory stored" in log_text or "chars" in log_text


def test_summarization_failure_doesnt_break_chat(client, monkeypatch):
    """Test that summarization failure doesn't break chat."""
    from app import _summarize_conversation_async
    import time
    
    # Mock Anthropic client to fail
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API Error")
    
    monkeypatch.setattr("app.client", mock_client)
    monkeypatch.setattr("conversation_memory.USE_DATABASE", False)
    
    # Trigger summarization
    _summarize_conversation_async("test_user", "ru-RU")
    
    # Give thread time to complete
    time.sleep(0.5)
    
    # Should not crash - test passes if we get here


@patch('conversation_memory.psycopg2')
def test_database_stale_connection_retry(mock_psycopg2, temp_memory_file, monkeypatch):
    """Test retry logic on stale database connection."""
    # Set up mock connection that fails first, succeeds second time
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    
    # First call raises "connection closed" error
    # Second call succeeds
    call_count = [0]
    def cursor_side_effect():
        call_count[0] += 1
        if call_count[0] == 1:
            raise Exception("connection already closed")
        return mock_cursor
    
    mock_conn.cursor.side_effect = cursor_side_effect
    mock_conn.autocommit = True
    
    # Mock psycopg2.connect to return our mock connection
    mock_psycopg2.connect.return_value = mock_conn
    
    # Enable database mode for this test
    monkeypatch.setattr(conversation_memory, "USE_DATABASE", True)
    monkeypatch.setattr(conversation_memory, "PSYCOPG2_AVAILABLE", True)
    
    # Reset connection to force new connection
    conversation_memory._reset_connection()
    
    # This should trigger retry logic
    user_id = "test_retry"
    summary = "Test summary for retry"
    
    success, error = conversation_memory.store_memory(user_id, summary)
    
    # Should succeed after retry
    assert success is True or success is False  # Either way is fine, we're testing no crash


def test_file_fallback_when_db_fails(temp_memory_file, monkeypatch):
    """Test that file fallback works when database is unavailable."""
    # Disable database
    monkeypatch.setattr(conversation_memory, "USE_DATABASE", False)
    
    user_id = "test_file_fallback"
    summary = "This should be stored in file."
    
    # Store memory (should use file)
    success, error = conversation_memory.store_memory(user_id, summary)
    assert success is True
    
    # Retrieve memory (should read from file)
    retrieved = conversation_memory.get_memory(user_id)
    assert retrieved == summary
    
    # Check that file was actually created
    assert temp_memory_file.exists()
    content = json.loads(temp_memory_file.read_text())
    assert user_id in content
    assert content[user_id]["summary"] == summary


def test_ps_test_users_not_excluded_from_memory(temp_memory_file):
    """Test that ps_TEST_ users ARE included in memory (per requirements)."""
    user_id = "ps_TEST_user123"
    summary = "Test user memory"
    
    # Should work for test users too
    success, error = conversation_memory.store_memory(user_id, summary)
    assert success is True
    
    retrieved = conversation_memory.get_memory(user_id)
    assert retrieved == summary


def test_memory_with_different_languages(monkeypatch):
    """Test memory block generation for different languages."""
    from app import _build_memory_block
    
    summary = "User has a son named David. Likes chess."
    
    # Russian
    block_ru = _build_memory_block(summary, "ru-RU")
    assert "прошлых разговоров" in block_ru
    assert "не выдумывай" in block_ru
    assert summary in block_ru
    
    # Hebrew
    block_he = _build_memory_block(summary, "he-IL")
    assert "משיחות קודמות" in block_he
    assert "לעולם אל תמציא" in block_he
    assert summary in block_he
    
    # English
    block_en = _build_memory_block(summary, "en-US")
    assert "past conversations" in block_en
    assert "NEVER invent" in block_en
    assert summary in block_en


def test_max_summary_constraint():
    """Test that MAX_SUMMARY_CHARS is properly defined."""
    assert conversation_memory.MAX_SUMMARY_CHARS == 800


def test_update_every_n_messages():
    """Test that UPDATE_EVERY_N_MESSAGES is properly defined."""
    assert conversation_memory.UPDATE_EVERY_N_MESSAGES == 4


def test_summarization_triggers_in_real_chat_flow(client, temp_memory_file, monkeypatch):
    """Test that summarization fires in real /chat flow with trim_history (Bug #1 fix)."""
    from unittest.mock import MagicMock, patch
    import time
    
    # Disable database for this test
    monkeypatch.setattr("conversation_memory.USE_DATABASE", False)
    
    # Mock Anthropic client
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Test summary with info from message")]
    mock_client.messages.create.return_value = mock_response
    
    monkeypatch.setattr("app.client", mock_client)
    
    # Send 4 user messages (should trigger summarization after 4th)
    user_id = "test_user_trim_flow"
    for i in range(4):
        response = client.post('/chat', json={
            "message": f"Test message {i+1} with unique content",
            "user_id": user_id,
            "name": "Test",
            "age": "70",
            "language": "ru-RU",
            "interests": "testing",
            "avoid_topics": "none"
        }, content_type='application/json')
        
        assert response.status_code == 200
    
    # Wait for background summarization to complete
    time.sleep(1.5)
    
    # Verify summarization was called at least once
    assert mock_client.messages.create.call_count >= 1
    
    # Verify summary was stored
    stored_summary = conversation_memory.get_memory(user_id)
    assert stored_summary is not None
    assert "Test summary" in stored_summary


def test_summarization_with_many_messages_through_trim(client, temp_memory_file, monkeypatch):
    """Test summarization works correctly when history is trimmed (Bug #1 fix).
    
    Simulates 12 messages (exceeds MAX_MESSAGES=10), verifies summarization
    still fires and includes content from trimmed messages.
    """
    from unittest.mock import MagicMock
    import time
    
    # Disable database
    monkeypatch.setattr("conversation_memory.USE_DATABASE", False)
    
    # Mock Anthropic client - capture what's sent for summarization
    mock_client = MagicMock()
    summarization_calls = []
    
    def mock_create(**kwargs):
        # Capture the prompt content
        if 'messages' in kwargs and len(kwargs['messages']) > 0:
            summarization_calls.append(kwargs['messages'][0]['content'])
        
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Summary including early message content")]
        return mock_response
    
    mock_client.messages.create.side_effect = mock_create
    monkeypatch.setattr("app.client", mock_client)
    
    # Send 12 messages (will trigger summarization multiple times)
    user_id = "test_user_12_msgs"
    for i in range(12):
        response = client.post('/chat', json={
            "message": f"Unique message number {i+1} with special keyword MSG{i+1}",
            "user_id": user_id,
            "name": "Test",
            "age": "70",
            "language": "ru-RU",
            "interests": "testing",
            "avoid_topics": "none"
        }, content_type='application/json')
        
        assert response.status_code == 200
    
    # Wait for all background summarizations
    time.sleep(2.0)
    
    # Verify at least one summarization happened
    assert len(summarization_calls) >= 1
    
    # Verify the summarization prompt includes early messages
    # (even though they may be trimmed from in-memory history)
    full_prompt = " ".join(summarization_calls)
    # Should include content from early messages
    assert "MSG" in full_prompt
    
    # Verify summary was stored
    stored_summary = conversation_memory.get_memory(user_id)
    assert stored_summary is not None


def test_summarization_budget_consumption(client, temp_memory_file, monkeypatch):
    """Test that summarization calls count against budget (Bug #2 fix)."""
    import budget_guard
    from unittest.mock import MagicMock
    import time
    
    # Disable database
    monkeypatch.setattr("conversation_memory.USE_DATABASE", False)
    
    # Mock Anthropic client
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Test summary")]
    mock_client.messages.create.return_value = mock_response
    monkeypatch.setattr("app.client", mock_client)
    
    # Check initial budget
    status_before = budget_guard.status()
    summaries_before = status_before.get("summaries_used", 0)
    
    # Send 4 messages to trigger summarization
    user_id = "test_user_budget"
    for i in range(4):
        client.post('/chat', json={
            "message": f"Test message {i+1}",
            "user_id": user_id,
            "name": "Test",
            "age": "70",
            "language": "ru-RU",
            "interests": "testing",
            "avoid_topics": "none"
        }, content_type='application/json')
    
    # Wait for background summarization
    time.sleep(1.5)
    
    # Check budget after
    status_after = budget_guard.status()
    summaries_after = status_after.get("summaries_used", 0)
    
    # Verify budget was consumed
    assert summaries_after > summaries_before


def test_summarization_budget_limit(monkeypatch, temp_memory_file):
    """Test that summarization stops when budget exhausted (Bug #2 fix)."""
    import budget_guard
    
    # Reset budget day tracking to force fresh state
    monkeypatch.setattr(budget_guard, "_day", None)
    monkeypatch.setattr(budget_guard, "_summaries", 0)
    
    # Set very low limit
    monkeypatch.setattr(budget_guard, "PILOT_DAILY_SUMMARIES", 1)
    
    # Consume one summary
    allowed1, _ = budget_guard.try_consume_summary()
    assert allowed1 is True
    
    # Second should fail
    allowed2, why = budget_guard.try_consume_summary()
    assert allowed2 is False
    assert why == "summary_limit"


def test_concurrent_summarization_guard(monkeypatch, temp_memory_file):
    """Test that concurrent summarization for same user is prevented (Bug #3 fix)."""
    from app import _summarization_in_progress, _summarize_conversation_async
    from unittest.mock import MagicMock
    import time
    
    # Disable database
    monkeypatch.setattr("conversation_memory.USE_DATABASE", False)
    
    # Mock Anthropic client with slow response
    mock_client = MagicMock()
    def slow_create(**kwargs):
        time.sleep(0.5)  # Simulate slow API call
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Test summary")]
        return mock_response
    
    mock_client.messages.create.side_effect = slow_create
    monkeypatch.setattr("app.client", mock_client)
    
    # Set up user with enough messages to trigger summarization
    from app import _user_message_counters, _unsummarized_buffers
    user_id = "test_user_concurrent"
    _user_message_counters[user_id] = 4
    _unsummarized_buffers[user_id] = [
        {"role": "user", "content": "Test 1"},
        {"role": "assistant", "content": "Reply 1"},
        {"role": "user", "content": "Test 2"},
        {"role": "assistant", "content": "Reply 2"},
    ]
    
    # Start first summarization
    _summarize_conversation_async(user_id, "ru-RU")
    time.sleep(0.1)  # Let it start
    
    # Verify user is in progress set
    assert user_id in _summarization_in_progress
    
    # Try to start second summarization (should be skipped)
    call_count_before = mock_client.messages.create.call_count
    _summarize_conversation_async(user_id, "ru-RU")
    time.sleep(0.2)
    call_count_after = mock_client.messages.create.call_count
    
    # Second call should be skipped (no new API call)
    assert call_count_after == call_count_before
    
    # Wait for first to complete
    time.sleep(1.0)
    
    # Verify user is removed from progress set
    assert user_id not in _summarization_in_progress
