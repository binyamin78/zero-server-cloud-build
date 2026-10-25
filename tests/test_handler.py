import io
import os
import sys

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import handler  # noqa: E402


class FakeS3:
    def __init__(self, files):
        self.files = dict(files)
        self.copied = []
        self.deleted = []

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.files[(Bucket, Key)])}

    def copy_object(self, Bucket, CopySource, Key):
        self.copied.append((Bucket, Key))

    def delete_object(self, Bucket, Key):
        self.deleted.append((Bucket, Key))


class FakeSNS:
    def __init__(self):
        self.messages = []

    def publish(self, TopicArn, Subject, Message):
        self.messages.append(Subject)


def event(*keys):
    return {
        "Records": [
            {"s3": {"bucket": {"name": "inbox"}, "object": {"key": k}}} for k in keys
        ]
    }


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("OUTPUT_BUCKET", "out")
    monkeypatch.setenv("TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:t")
    sns = FakeSNS()
    monkeypatch.setattr(handler, "sns", sns)

    def install(files):
        s3 = FakeS3({("inbox", k): v for k, v in files.items()})
        monkeypatch.setattr(handler, "s3", s3)
        return s3, sns

    return install


def test_valid_file_goes_to_processed(aws):
    s3, sns = aws({"report.csv": b"id,status\n1,pass\n"})
    result = handler.lambda_handler(event("report.csv"), None)

    assert s3.copied == [("out", "processed/report.csv")]
    assert s3.deleted == [("inbox", "report.csv")]
    assert result["results"][0]["reason"] is None
    assert sns.messages == ["File processed: report.csv"]


@pytest.mark.parametrize(
    "key, body, reason",
    [
        ("photo.png", b"\x89PNG", "unsupported file type"),
        ("empty.txt", b"   \n", "file is empty"),
        ("binary.txt", b"\xff\xfe\x00", "not UTF-8"),
    ],
)
def test_invalid_files_go_to_rejected(aws, key, body, reason):
    s3, _ = aws({key: body})
    result = handler.lambda_handler(event(key), None)

    assert s3.copied == [("out", f"rejected/{key}")]
    assert reason in result["results"][0]["reason"]


def test_url_encoded_key_is_decoded(aws):
    s3, _ = aws({"daily report.txt": b"ok"})
    handler.lambda_handler(event("daily+report.txt"), None)

    assert s3.copied == [("out", "processed/daily report.txt")]


def test_every_file_in_the_event_is_handled(aws):
    s3, sns = aws({"a.json": b"{}", "b.exe": b"MZ"})
    handler.lambda_handler(event("a.json", "b.exe"), None)

    assert s3.copied == [("out", "processed/a.json"), ("out", "rejected/b.exe")]
    assert len(sns.messages) == 2
