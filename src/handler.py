"""Sort files that land in the inbox bucket and report the result by email.

Triggered by S3 ObjectCreated events. Each file is validated, copied to
processed/ or rejected/ in the output bucket, removed from the inbox, and
summarised in an SNS notification.
"""
import os
import urllib.parse

import boto3

ALLOWED_EXTENSIONS = (".csv", ".txt", ".json")

s3 = boto3.client("s3")
sns = boto3.client("sns")


def validate(key, body):
    """Return None if the file is valid, otherwise the reason it was rejected."""
    if not key.lower().endswith(ALLOWED_EXTENSIONS):
        return f"unsupported file type (allowed: {', '.join(ALLOWED_EXTENSIONS)})"
    if not body.strip():
        return "file is empty"
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        return "file is not UTF-8 text"
    return None


def lambda_handler(event, context):
    output_bucket = os.environ["OUTPUT_BUCKET"]
    topic_arn = os.environ["TOPIC_ARN"]
    results = []

    for record in event["Records"]:
        source_bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])

        body = s3.get_object(Bucket=source_bucket, Key=key)["Body"].read()
        reason = validate(key, body)
        destination = "rejected" if reason else "processed"

        s3.copy_object(
            Bucket=output_bucket,
            CopySource={"Bucket": source_bucket, "Key": key},
            Key=f"{destination}/{key}",
        )
        s3.delete_object(Bucket=source_bucket, Key=key)

        line = f"{key}: {destination}" + (f" ({reason})" if reason else "")
        print(line)
        results.append({"key": key, "destination": destination, "reason": reason})

        sns.publish(
            TopicArn=topic_arn,
            Subject=f"File {destination}: {key}"[:100],
            Message=line,
        )

    return {"statusCode": 200, "results": results}
