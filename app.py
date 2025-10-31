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
from typing import List, Dict, Any, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, EndpointConnectionError
from botocore.config import Config
from flask import Flask, render_template_string, send_from_directory
from kubernetes import client, config as k8s_config
from kubernetes.client.rest import ApiException


app = Flask(__name__)


@app.route('/background.png')
def background_image():
    """Serve the background image."""
    return send_from_directory('.', 'background.png')


# קריאת קרדנציאלס והאזור מהסביבה (עם ברירות מחדל)
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")


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


def _has_credentials(session: boto3.session.Session) -> bool:
    """בודק בפועל קרדנציאלס על ידי קריאת STS קצרה.

    שימוש ב‑get_credentials בלבד עלול להחזיר אובייקט דחוי גם בלי הרשאות בפועל,
    לכן נבדוק `sts:GetCallerIdentity` עם timeouts קצרים.
    """
    try:
        cfg = Config(read_timeout=2, connect_timeout=1, retries={"max_attempts": 0})
        sts = session.client("sts", config=cfg)
        sts.get_caller_identity()
        return True
    except (NoCredentialsError, ClientError, BotoCoreError, EndpointConnectionError):
        return False


def _safe_call(fn, fallback: Any):
    """מריץ קריאת AWS באופן בטוח ומחזיר מבנה ברירת מחדל אם יש שגיאה.

    זה מאפשר לדף להיטען ולהציג הודעה ידידותית במקום להתרסק ב‑500/traceback.
    """
    try:
        return fn()
    except (ClientError, BotoCoreError, NoCredentialsError) as e:
        # נחזיר מבנה פשוט שהטמפלייט מסוגל לרנדר
        return {"__error__": str(e)} if isinstance(fallback, dict) else fallback


def get_kubernetes_info() -> Dict[str, Any]:
    """מקבל מידע על PODs מ-Kubernetes cluster.
    
    Returns:
        Dict עם:
        - pod_count: מספר PODs פעילים
        - current_pod: שם ה-POD הנוכחי
        - error: הודעת שגיאה אם יש
    """
    try:
        # נסה לטעון את ה-config מתוך הקלאסטר (in-cluster config)
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        # אם לא רץ בתוך קלאסטר, נסה config מקומי
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
        
        # קבל את שם ה-POD הנוכחי ממשתנה סביבה
        current_pod_name = os.getenv("HOSTNAME", "unknown")
        
        # קבל namespace (ברירת מחדל: default)
        namespace = os.getenv("POD_NAMESPACE", "default")
        
        # ספור PODs פעילים עם אותו label
        # נניח שה-label הוא app=aws-resources-viewer
        label_selector = "app.kubernetes.io/name=aws-resources-viewer"
        pods = v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector
        )
        
        # ספור רק PODs במצב Running
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
    """דף הבית: איסוף נתונים מהשירותים ורנדרינג של הטבלאות.

    אם אין קרדנציאלס — מוצגים נתוני דמו כדי לאפשר הרצה ללא הכנה מיוחדת.
    """
    # מצב תרגיל: שחזור שגיאת NameError כפי בדרישה (באופן מבוקר)
    # הפעלה עם: SHOW_BUG=1
    show_bug = os.getenv("SHOW_BUG", "").lower() in ("1", "true", "yes", "on")
    if show_bug:
        # שחזור השורה הבעייתית המקורית ליצירת NameError: 'vpcs' לא מוגדר
        # הערה: הקוד למטה נועד רק לייצר את השגיאה עבור התרגיל
        vpc_data = [{"VPC ID": vpc["VpcId"], "CIDR": vpc["CidrBlock"]} for vpc in vpcs["Vpcs"]]  # noqa: F821
        return str(vpc_data)  # לא יגיע לכאן — NameError ייזרק קודם
    # קבל מידע על Kubernetes PODs
    k8s_info = get_kubernetes_info()
    
    session = _boto3_session()
    demo_mode = not _has_credentials(session)

    if demo_mode:
        # נתוני דמו בסיסיים לצורך תצוגה כאשר אין קרדנציאלס
        instance_data: List[Dict[str, Any]] = [
            {"ID": "i-0demo123", "State": "running", "Type": "t3.micro", "Public IP": "203.0.113.10"}
        ]
        vpc_data = [{"VPC ID": "vpc-0demo123", "CIDR": "10.0.0.0/16"}]
        lb_data = [{"LB Name": "alb-demo", "DNS Name": "alb-demo-123.elb.amazonaws.com"}]
        ami_data = [{"AMI ID": "ami-0demo123", "Name": "demo-ami"}]
    else:
        ec2_client = session.client("ec2")
        elb_client = session.client("elbv2")

        # שלב 1: הבאת אינסטנסים של EC2
        instances_resp = _safe_call(lambda: ec2_client.describe_instances(), {"Reservations": []})
        instance_data = []  # type: List[Dict[str, Any]]
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
            vpc_data = [
                {"VPC ID": vpc.get("VpcId", "N/A"), "CIDR": vpc.get("CidrBlock", "N/A")} for vpc in vpcs_resp.get("Vpcs", [])
            ]

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
            ami_data = [
                {"AMI ID": ami.get("ImageId", "N/A"), "Name": ami.get("Name", "N/A")} for ami in amis_resp.get("Images", [])
            ]

        # אם התגלה חוסר קרדנציאלס במהלך הקריאות (למשל בקונטיינר/IMDS), מעבר לדמו
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

    # רנדרינג: תבנית HTML מעוצבת עם לוגואים
    html_template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>AWS Resources Viewer</title>
        <!-- Auto-refresh disabled to reduce server load -->
        <!-- <meta http-equiv="refresh" content="5"> -->
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background-image: url('/background.png');
                background-size: cover;
                background-position: center;
                background-attachment: fixed;
                background-repeat: no-repeat;
                padding: 20px;
                min-height: 100vh;
                position: relative;
            }
            
            body::before {
                content: '';
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(0, 0, 0, 0.3);
                z-index: -1;
            }
            
            .container {
                max-width: 1400px;
                margin: 0 auto;
                background: white;
                border-radius: 15px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                overflow: hidden;
            }
            
            .header {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 30px;
                text-align: center;
            }
            
            .header h1 {
                font-size: 2.5em;
                margin-bottom: 10px;
                text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
            }
            
            .header .subtitle {
                font-size: 1.2em;
                opacity: 0.9;
            }
            
            .tech-logos {
                display: flex;
                justify-content: center;
                gap: 30px;
                margin-top: 20px;
                flex-wrap: wrap;
            }
            
            .tech-logo {
                background: white;
                padding: 10px 20px;
                border-radius: 10px;
                display: flex;
                align-items: center;
                gap: 10px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                transition: transform 0.3s;
            }
            
            .tech-logo:hover {
                transform: translateY(-5px);
            }
            
            .tech-logo img {
                height: 30px;
            }
            
            .tech-logo span {
                font-weight: bold;
                color: #333;
            }
            
            .content {
                padding: 30px;
            }
            
            .demo-notice {
                padding: 15px;
                margin-bottom: 20px;
                border: 2px solid #ffa726;
                background: #fff3e0;
                border-radius: 10px;
                text-align: center;
                font-weight: bold;
                color: #e65100;
            }
            
            .keda-info {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 25px;
                margin-bottom: 30px;
                border-radius: 15px;
                box-shadow: 0 10px 30px rgba(102, 126, 234, 0.4);
            }
            
            .keda-info h2 {
                font-size: 2em;
                margin-bottom: 20px;
                display: flex;
                align-items: center;
                gap: 10px;
            }
            
            .keda-stats {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-top: 20px;
            }
            
            .stat-card {
                background: rgba(255, 255, 255, 0.2);
                padding: 20px;
                border-radius: 10px;
                backdrop-filter: blur(10px);
                border: 1px solid rgba(255, 255, 255, 0.3);
            }
            
            .stat-label {
                font-size: 0.9em;
                opacity: 0.9;
                margin-bottom: 5px;
            }
            
            .stat-value {
                font-size: 2em;
                font-weight: bold;
            }
            
            .refresh-notice {
                text-align: center;
                margin-top: 15px;
                font-size: 0.9em;
                opacity: 0.8;
            }
            
            .section {
                margin-bottom: 40px;
            }
            
            .section h2 {
                color: #667eea;
                font-size: 1.8em;
                margin-bottom: 15px;
                padding-bottom: 10px;
                border-bottom: 3px solid #667eea;
            }
            
            table {
                width: 100%;
                border-collapse: collapse;
                background: white;
                border-radius: 10px;
                overflow: hidden;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }
            
            th {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 15px;
                text-align: left;
                font-weight: 600;
                text-transform: uppercase;
                font-size: 0.9em;
                letter-spacing: 1px;
            }
            
            td {
                padding: 15px;
                border-bottom: 1px solid #f0f0f0;
                color: #333;
            }
            
            tr:hover {
                background: #f8f9ff;
            }
            
            tr:last-child td {
                border-bottom: none;
            }
            
            .footer {
                background: #f5f5f5;
                padding: 20px;
                text-align: center;
                color: #666;
                font-size: 0.9em;
            }
            
            @keyframes pulse {
                0%, 100% { opacity: 1; }
                50% { opacity: 0.5; }
            }
            
            .live-indicator {
                display: inline-block;
                width: 10px;
                height: 10px;
                background: #4CAF50;
                border-radius: 50%;
                margin-right: 5px;
                animation: pulse 2s infinite;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>AWS Resources Viewer</h1>
                <p class="subtitle">Real-time Kubernetes Auto-Scaling with KEDA</p>
                <div class="tech-logos">
                    <div class="tech-logo">
                        <span>☸️</span>
                        <span>Kubernetes</span>
                    </div>
                    <div class="tech-logo">
                        <span>📊</span>
                        <span>KEDA</span>
                    </div>
                    <div class="tech-logo">
                        <span>☁️</span>
                        <span>AWS</span>
                    </div>
                    <div class="tech-logo">
                        <span>🐍</span>
                        <span>Flask</span>
                    </div>
                    <div class="tech-logo">
                        <span>🔄</span>
                        <span>ArgoCD</span>
                    </div>
                </div>
            </div>
            
            <div class="content">
                {% if demo_mode %}
                <div class="demo-notice">
                    ⚠️ Running in demo mode (no AWS credentials detected). Showing sample data.
                </div>
                {% endif %}
                
                <div class="keda-info">
                    <h2>
                        <span class="live-indicator"></span>
                        KEDA Auto-Scaling Status
                    </h2>
                    <div class="keda-stats">
                        <div class="stat-card">
                            <div class="stat-label">Active Pods</div>
                            <div class="stat-value">{{ k8s_info.pod_count }}</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Current Pod</div>
                            <div class="stat-value" style="font-size: 1.2em;">{{ k8s_info.current_pod }}</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Namespace</div>
                            <div class="stat-value" style="font-size: 1.5em;">{{ k8s_info.get('namespace', 'default') }}</div>
                        </div>
                    </div>
                    {% if k8s_info.error %}
                    <div style="margin-top: 15px; padding: 10px; background: rgba(255,255,255,0.2); border-radius: 5px;">
                        <strong>⚠️ Note:</strong> {{ k8s_info.error }}
                    </div>
                    {% endif %}
                    <div class="refresh-notice">
                        💡 Refresh the page (F5) to see updated POD count
                    </div>
                </div>
                
                <div class="section">
                    <h2>💻 Running EC2 Instances</h2>
                    <table>
                        <thead>
                            <tr>
                                <th>Instance ID</th>
                                <th>State</th>
                                <th>Type</th>
                                <th>Public IP</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for instance in instance_data %}
                            <tr>
                                <td>{{ instance['ID'] }}</td>
                                <td>{{ instance['State'] }}</td>
                                <td>{{ instance['Type'] }}</td>
                                <td>{{ instance['Public IP'] }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>

                <div class="section">
                    <h2>🌐 Virtual Private Clouds (VPCs)</h2>
                    <table>
                        <thead>
                            <tr>
                                <th>VPC ID</th>
                                <th>CIDR Block</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for vpc in vpc_data %}
                            <tr>
                                <td>{{ vpc['VPC ID'] }}</td>
                                <td>{{ vpc['CIDR'] }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>

                <div class="section">
                    <h2>⚖️ Load Balancers</h2>
                    <table>
                        <thead>
                            <tr>
                                <th>Load Balancer Name</th>
                                <th>DNS Name</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for lb in lb_data %}
                            <tr>
                                <td>{{ lb['LB Name'] }}</td>
                                <td>{{ lb['DNS Name'] }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>

                <div class="section">
                    <h2>📀 Available AMIs</h2>
                    <table>
                        <thead>
                            <tr>
                                <th>AMI ID</th>
                                <th>Name</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for ami in ami_data %}
                            <tr>
                                <td>{{ ami['AMI ID'] }}</td>
                                <td>{{ ami['Name'] }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
            
            <div class="footer">
                <p><strong>JB Student Project</strong> | Powered by Kubernetes, KEDA, AWS, Flask & ArgoCD</p>
                <p style="margin-top: 5px;">Built with ❤️ for demonstrating auto-scaling capabilities</p>
            </div>
        </div>
    </body>
    </html>
    """

    return render_template_string(
        html_template,
        instance_data=instance_data,
        vpc_data=vpc_data,
        lb_data=lb_data,
        ami_data=ami_data,
        demo_mode=demo_mode,
        k8s_info=k8s_info,
    )


if __name__ == "__main__":
    # הפעלת שרת Flask המובנה (מתאים לדמו/פיתוח).
    # בייצור נהוג להשתמש ב‑gunicorn/uwsgi מאחורי Reverse Proxy.
    app.run(host="0.0.0.0", port=5001, debug=False)
