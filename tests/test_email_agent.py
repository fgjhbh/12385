import pytest

from email_agent import (
    EmailAgent,
    EmailTask,
    append_signature,
    compose_from_goal,
    infer_subject,
    parse_variables,
    render_template,
    validate_email_address,
)


def test_agent_drafts_from_goal_with_signature():
    task = EmailTask(
        to=["client@example.com"],
        sender="me@example.com",
        goal="提醒客户明天下午三点开会",
        signature="小王",
    )

    draft = EmailAgent().draft(task)

    assert draft.subject == "提醒客户明天下午三点开会"
    assert "提醒客户明天下午三点开会" in draft.body
    assert draft.body.endswith("小王")


def test_agent_renders_template_variables():
    task = EmailTask(
        to=["team@example.com"],
        sender="me@example.com",
        subject="会议提醒",
        template="Hi $name, meeting is at $time.",
        variables={"name": "Ada", "time": "3pm"},
    )

    draft = EmailAgent().draft(task)

    assert draft.body == "Hi Ada, meeting is at 3pm."


def test_agent_builds_message_without_bcc_header():
    task = EmailTask(
        to=["to@example.com"],
        cc=["cc@example.com"],
        bcc=["hidden@example.com"],
        sender="me@example.com",
        subject="Hello",
        body="Body",
    )
    agent = EmailAgent()
    draft = agent.draft(task)

    message = agent.build_message(task, draft)
    preview = agent.preview(task, draft)

    assert message["To"] == "to@example.com"
    assert message["Cc"] == "cc@example.com"
    assert "Bcc" not in message
    assert "1 位收件人" in preview


def test_invalid_email_is_rejected():
    with pytest.raises(ValueError, match="邮箱地址无效"):
        validate_email_address("not-an-email", "to")


def test_draft_requires_body_template_or_goal():
    task = EmailTask(to=["to@example.com"], sender="me@example.com")

    with pytest.raises(ValueError, match="请提供"):
        EmailAgent().draft(task)


def test_parse_variables_from_file_and_cli(tmp_path):
    variables_file = tmp_path / "vars.json"
    variables_file.write_text('{"name": "Ada", "time": "2pm"}', encoding="utf-8")

    variables = parse_variables(["time=3pm", "place=Zoom"], str(variables_file))

    assert variables == {"name": "Ada", "time": "3pm", "place": "Zoom"}


def test_render_template_uses_safe_substitute():
    assert render_template("Hi $name $missing", {"name": "Ada"}) == "Hi Ada $missing"


def test_compose_from_goal_supports_concise_tone():
    assert compose_from_goal("请确认报价", "concise") == "您好，\n\n请确认报价。\n\n谢谢，"


def test_append_signature_strips_extra_space():
    assert append_signature("Body\n", " Me ") == "Body\n\nMe"


def test_infer_subject_uses_first_non_empty_line():
    assert infer_subject(None, "\n  First line\nSecond", None) == "First line"
