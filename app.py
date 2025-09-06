"""
אפליקציית Flask לדוגמה להצגת משאבי AWS.

מה האפליקציה עושה:
- מציגה טבלאות של EC2 Instances, VPCs, Load Balancers (ALB/NLB) ו‑AMIs בבעלות החשבון.
- משתמשת ב‑boto3 לקריאות ל‑AWS באזור us‑east‑1.
- מיועדת להרצה בתוך Docker על פורט 5001.

קרדנציאלס/אזור:
- שימוש במשתני סביבה: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY.
- אזור ברירת מחדל: us‑east‑1.

הערה היסטורית על באג שתוקן:
- בגרסה קודמת נעשה שימוש במשתנים לא מוגדרים vpcs / lbs / amis ולכן התקבלה NameError.
- התיקון: קריאות מפורשות ל‑describe_vpcs, describe_load_balancers, describe_images
  ומעבר על התוצאות. בנוסף יש עטיפת שגיאות כדי שהעמוד לא יקרוס גם ללא הרשאות.
"""

import os
from typing import List, Dict, Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from flask import Flask, render_template_string


app = Flask(__name__)


# קריאת קרדנציאלס מה־ENV של הקונטיינר. האזור מקובע כברירת מחדל
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
REGION = "us-east-1"


def _boto3_session():
    """יוצר Session של boto3 לשימוש בקליינטים.

    שימו לב: אם הערכים שנשלחים הם None, boto3 משתמש ב‑default credential chain
    (למשל IAM Role), כך שניתן להריץ גם ללא העברת משתנים מפורשים כאשר יש תפקיד מתאים.
    """
    return boto3.Session(
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        region_name=REGION,
    )


def _safe_call(fn, fallback: Any):
    """מריץ קריאת AWS באופן בטוח ומחזיר מבנה ברירת מחדל אם יש שגיאה.

    זה מאפשר לדף להיטען ולהציג הודעה ידידותית במקום להתרסק ב‑500/traceback.
    """
    try:
        return fn()
    except (ClientError, BotoCoreError, NoCredentialsError) as e:
        # נחזיר מבנה פשוט שהטמפלייט מסוגל לרנדר
        return {"__error__": str(e)} if isinstance(fallback, dict) else fallback


@app.route("/")
def home():
    """דף הבית: איסוף נתונים מהשירותים ורנדרינג של הטבלאות."""
    session = _boto3_session()
    ec2_client = session.client("ec2")
    elb_client = session.client("elbv2")

    # שלב 1: הבאת אינסטנסים של EC2
    instances_resp = _safe_call(lambda: ec2_client.describe_instances(), {"Reservations": []})
    instance_data: List[Dict[str, Any]] = []
    if "__error__" in instances_resp:
        instance_data = [{"ID": "-", "State": instances_resp["__error__"], "Type": "-", "Public IP": "-"}]
    else:
        for reservation in instances_resp.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                instance_data.append(
                    {
                        "ID": instance.get("InstanceId", "N/A"),
                        "State": instance.get("State", {}).get("Name", "N/A"),
                        "Type": instance.get("InstanceType", "N/A"),
                        "Public IP": instance.get("PublicIpAddress", "N/A"),
                    }
                )

    # שלב 2: הבאת רשימת VPCs
    vpcs_resp = _safe_call(lambda: ec2_client.describe_vpcs(), {"Vpcs": []})
    if "__error__" in vpcs_resp:
        vpc_data = [{"VPC ID": "-", "CIDR": vpcs_resp["__error__"]}]
    else:
        vpc_data = [{"VPC ID": vpc.get("VpcId", "N/A"), "CIDR": vpc.get("CidrBlock", "N/A")} for vpc in vpcs_resp.get("Vpcs", [])]

    # שלב 3: הבאת Load Balancers (ALB/NLB) דרך elbv2
    lbs_resp = _safe_call(lambda: elb_client.describe_load_balancers(), {"LoadBalancers": []})
    if "__error__" in lbs_resp:
        lb_data = [{"LB Name": "-", "DNS Name": lbs_resp["__error__"]}]
    else:
        lb_data = [
            {"LB Name": lb.get("LoadBalancerName", "N/A"), "DNS Name": lb.get("DNSName", "N/A")} for lb in lbs_resp.get("LoadBalancers", [])
        ]

    # שלב 4: הבאת AMIs בבעלות החשבון בלבד
    amis_resp = _safe_call(lambda: ec2_client.describe_images(Owners=["self"]), {"Images": []})
    if "__error__" in amis_resp:
        ami_data = [{"AMI ID": "-", "Name": amis_resp["__error__"]}]
    else:
        ami_data = [{"AMI ID": ami.get("ImageId", "N/A"), "Name": ami.get("Name", "N/A")} for ami in amis_resp.get("Images", [])]

    # רנדרינג: תבנית HTML פשוטה שמציגה את הנתונים בטבלאות
    html_template = """
    <html>
    <head><title>AWS Resources</title></head>
    <body>
        <h1>Running EC2 Instances</h1>
        <table border='1'>
            <tr><th>ID</th><th>State</th><th>Type</th><th>Public IP</th></tr>
            {% for instance in instance_data %}
            <tr><td>{{ instance['ID'] }}</td><td>{{ instance['State'] }}</td><td>{{ instance['Type'] }}</td><td>{{ instance['Public IP'] }}</td></tr>
            {% endfor %}
        </table>

        <h1>VPCs</h1>
        <table border='1'>
            <tr><th>VPC ID</th><th>CIDR</th></tr>
            {% for vpc in vpc_data %}
            <tr><td>{{ vpc['VPC ID'] }}</td><td>{{ vpc['CIDR'] }}</td></tr>
            {% endfor %}
        </table>

        <h1>Load Balancers</h1>
        <table border='1'>
            <tr><th>LB Name</th><th>DNS Name</th></tr>
            {% for lb in lb_data %}
            <tr><td>{{ lb['LB Name'] }}</td><td>{{ lb['DNS Name'] }}</td></tr>
            {% endfor %}
        </table>

        <h1>Available AMIs</h1>
        <table border='1'>
            <tr><th>AMI ID</th><th>Name</th></tr>
            {% for ami in ami_data %}
            <tr><td>{{ ami['AMI ID'] }}</td><td>{{ ami['Name'] }}</td></tr>
            {% endfor %}
        </table>
    </body>
    </html>
    """

    return render_template_string(
        html_template,
        instance_data=instance_data,
        vpc_data=vpc_data,
        lb_data=lb_data,
        ami_data=ami_data,
    )


if __name__ == "__main__":
    # הפעלת שרת Flask המובנה (מתאים לדמו/פיתוח).
    # בייצור נהוג להשתמש ב‑gunicorn/uwsgi מאחורי Reverse Proxy.
    app.run(host="0.0.0.0", port=5001, debug=True)
