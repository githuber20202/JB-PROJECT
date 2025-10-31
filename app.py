"""
אפליקציית Flask לדוגמה להצגת משאבי AWS.

מה האפליקציה עושה:
- מציגה טבלאות של EC2 Instances, VPCs, Load Balancers (ALB/NLB) ו‑AMIs בבעלות החשבון.
- משתמשת ב‑boto3 לקריאות ל‑AWS.
- מיועדת להרצה בתוך Docker על פורט 5001.

קרדנציאלס/אזור:
- שימוש במשתני סביבה: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (אופציונלי אם יש IAM Role).
- אזור: נקרא מ‑AWS_DEFAULT_REGION ואם לא קיים — us‑east‑1.

Demo Mode אוטומטי:
- אם לא אותרו קרדנציאלס זמינים (ENV/Role), האפליקציה לא תנסה לפנות ל‑AWS ותציג נתוני דמו
  במקום שגיאה. כך אפשר להריץ את הפרויקט "כמו שהוא" בכל מכונה.
"""

import os
from typing import List, Dict, Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, EndpointConnectionError
from botocore.config import Config
from flask import Flask, render_template
from kubernetes import client, config as k8s_config
from kubernetes.client.rest import ApiException


app = Flask(__name__)

# קריאת קרדנציאלס והאזור מהסביבה (עם ברירות מחדל)
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")


def _boto3_session():
    """יוצר Session של boto3 לשימוש בקליינטים."""
    return boto3.Session(
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        region_name=REGION,
    )


def _has_credentials(session: boto3.session.Session) -> bool:
    """בודק בפועל קרדנציאלס על ידי קריאת STS קצרה."""
    try:
        cfg = Config(read_timeout=2, connect_timeout=1, retries={"max_attempts": 0})
        sts = session.client("sts", config=cfg)
        sts.get_caller_identity()
        return True
    except (NoCredentialsError, ClientError, BotoCoreError, EndpointConnectionError):
        return False


def _safe_call(fn, fallback: Any):
    """מריץ קריאת AWS באופן בטוח ומחזיר מבנה ברירת מחדל אם יש שגיאה."""
    try:
        return fn()
    except (ClientError, BotoCoreError, NoCredentialsError) as e:
        return {"__error__": str(e)} if isinstance(fallback, dict) else fallback


def get_kubernetes_info() -> Dict[str, Any]:
    """מקבל מידע על PODs מ-Kubernetes cluster."""
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        try:
            k8s_config.load_kube_config()
        except k8s_config.ConfigException:
            return {
                "pod_count": 0,
                "current_pod": "N/A",
                "error": "Not running in Kubernetes cluster"
            }
    
    try:
        v1 = client.CoreV1Api()
        current_pod_name = os.getenv("HOSTNAME", "unknown")
        namespace = os.getenv("POD_NAMESPACE", "default")
        label_selector = "app.kubernetes.io/name=aws-resources-viewer"
        pods = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
        running_pods = [p for p in pods.items if p.status.phase == "Running"]
        
        return {
            "pod_count": len(running_pods),
            "current_pod": current_pod_name,
            "namespace": namespace,
            "error": None
        }
    except ApiException as e:
        return {
            "pod_count": 0,
            "current_pod": os.getenv("HOSTNAME", "N/A"),
            "error": f"Kubernetes API error: {e.status}"
        }
    except Exception as e:
        return {
            "pod_count": 0,
            "current_pod": os.getenv("HOSTNAME", "N/A"),
            "error": f"Error: {str(e)}"
        }


@app.route("/")
def home():
    """דף הבית: איסוף נתונים מהשירותים ורנדרינג של הטבלאות."""
    # מצב תרגיל: שחזור שגיאת NameError (באופן מבוקר)
    show_bug = os.getenv("SHOW_BUG", "").lower() in ("1", "true", "yes", "on")
    if show_bug:
        vpc_data = [{"VPC ID": vpc["VpcId"], "CIDR": vpc["CidrBlock"]} for vpc in vpcs["Vpcs"]]  # noqa: F821
        return str(vpc_data)
    
    k8s_info = get_kubernetes_info()
    session = _boto3_session()
    demo_mode = not _has_credentials(session)

    if demo_mode:
        instance_data: List[Dict[str, Any]] = [
            {"ID": "i-0demo123", "State": "running", "Type": "t3.micro", "Public IP": "203.0.113.10"}
        ]
        vpc_data = [{"VPC ID": "vpc-0demo123", "CIDR": "10.0.0.0/16"}]
        lb_data = [{"LB Name": "alb-demo", "DNS Name": "alb-demo-123.elb.amazonaws.com"}]
        ami_data = [{"AMI ID": "ami-0demo123", "Name": "demo-ami"}]
    else:
        ec2_client = session.client("ec2")
        elb_client = session.client("elbv2")

        instances_resp = _safe_call(lambda: ec2_client.describe_instances(), {"Reservations": []})
        instance_data = []
        if "__error__" in instances_resp:
            instance_data = [{"ID": "-", "State": instances_resp["__error__"], "Type": "-", "Public IP": "-"}]
        else:
            for reservation in instances_resp.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    instance_data.append({
                        "ID": instance.get("InstanceId", "N/A"),
                        "State": instance.get("State", {}).get("Name", "N/A"),
                        "Type": instance.get("InstanceType", "N/A"),
                        "Public IP": instance.get("PublicIpAddress", "N/A"),
                    })

        vpcs_resp = _safe_call(lambda: ec2_client.describe_vpcs(), {"Vpcs": []})
        if "__error__" in vpcs_resp:
            vpc_data = [{"VPC ID": "-", "CIDR": vpcs_resp["__error__"]}]
        else:
            vpc_data = [
                {"VPC ID": vpc.get("VpcId", "N/A"), "CIDR": vpc.get("CidrBlock", "N/A")} 
                for vpc in vpcs_resp.get("Vpcs", [])
            ]

        lbs_resp = _safe_call(lambda: elb_client.describe_load_balancers(), {"LoadBalancers": []})
        if "__error__" in lbs_resp:
            lb_data = [{"LB Name": "-", "DNS Name": lbs_resp["__error__"]}]
        else:
            lb_data = [
                {"LB Name": lb.get("LoadBalancerName", "N/A"), "DNS Name": lb.get("DNSName", "N/A")} 
                for lb in lbs_resp.get("LoadBalancers", [])
            ]

        amis_resp = _safe_call(lambda: ec2_client.describe_images(Owners=["self"]), {"Images": []})
        if "__error__" in amis_resp:
            ami_data = [{"AMI ID": "-", "Name": amis_resp["__error__"]}]
        else:
            ami_data = [
                {"AMI ID": ami.get("ImageId", "N/A"), "Name": ami.get("Name", "N/A")} 
                for ami in amis_resp.get("Images", [])
            ]

        errors = []
        for resp in (instances_resp, vpcs_resp, lbs_resp, amis_resp):
            if isinstance(resp, dict) and "__error__" in resp:
                errors.append(str(resp["__error__"]))
        if any("Unable to locate credentials" in e for e in errors):
            demo_mode = True
            instance_data = [{"ID": "i-0demo123", "State": "running", "Type": "t3.micro", "Public IP": "203.0.113.10"}]
            vpc_data = [{"VPC ID": "vpc-0demo123", "CIDR": "10.0.0.0/16"}]
            lb_data = [{"LB Name": "alb-demo", "DNS Name": "alb-demo-123.elb.amazonaws.com"}]
            ami_data = [{"AMI ID": "ami-0demo123", "Name": "demo-ami"}]

    return render_template(
        'index.html',
        instance_data=instance_data,
        vpc_data=vpc_data,
        lb_data=lb_data,
        ami_data=ami_data,
        demo_mode=demo_mode,
        k8s_info=k8s_info,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
