"""Generate the AWS architecture diagram (real AWS service icons).

Uses the `diagrams` library (mingrammer) + Graphviz. Run:

    pip install diagrams        # and have graphviz `dot` on PATH
    python docs/diagrams/architecture_aws.py

Outputs docs/diagrams/architecture_aws.png. Committed PNG is the rendered result;
this script is the source of truth so the diagram is reproducible.
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import EC2
from diagrams.aws.ml import Bedrock
from diagrams.aws.network import CloudFront, VPC
from diagrams.aws.security import IAM
from diagrams.aws.storage import S3
from diagrams.onprem.client import Users
from diagrams.programming.framework import React
from diagrams.programming.language import Python

GRAPH_ATTR = {
    "fontsize": "20",
    "labelloc": "t",
    "pad": "0.5",
    "splines": "spline",
    "nodesep": "0.6",
    "ranksep": "0.9",
}


def main() -> None:
    with Diagram(
        "WhisperX Video Subtitling — AWS Architecture",
        filename="docs/diagrams/architecture_aws",
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=GRAPH_ATTR,
    ):
        viewer = Users("Viewer / Creator")
        cdn = CloudFront("CloudFront\n(only public entry)")

        with Cluster("AWS Account (temp-account, us-east-1)"):
            bedrock = Bedrock("Bedrock\nClaude (translate)")
            bucket = S3("S3\nvideos + SRT/VTT")
            role = IAM("IAM role\nscoped S3 + Bedrock")

            with Cluster("Default VPC — public subnet"):
                with Cluster("EC2 c7i.2xlarge (CPU)"):
                    api = Python("FastAPI\nAPI + SPA")
                    worker = Python("Worker\nWhisperX + translate")

        # viewer path
        viewer >> Edge(label="HTTPS") >> cdn
        cdn >> Edge(label="HTTP + X-Origin-Secret\n(SG: CloudFront prefix list only)") >> api
        viewer >> Edge(label="presigned PUT/GET", style="dashed", color="darkgreen") >> bucket

        # internal
        api >> Edge(label="enqueue (DB state)", style="dashed") >> worker
        api >> Edge(label="presigned URL") >> bucket
        worker >> Edge(label="get video / put subtitles") >> bucket
        worker >> Edge(label="translate segments") >> bedrock
        api >> Edge(style="dotted", label="assumes") >> role

    print("wrote docs/diagrams/architecture_aws.png")


def deployment() -> None:
    """A tighter deployment-topology view emphasizing the locked-down origin."""
    with Diagram(
        "Deployment Topology — Locked-Down Origin",
        filename="docs/diagrams/deployment_aws",
        outformat="png",
        show=False,
        direction="LR",
        graph_attr=GRAPH_ATTR,
    ):
        viewer = Users("Internet\nviewers")
        cdn = CloudFront("CloudFront\ndistribution")

        with Cluster("AWS Account — temp-account"):
            with Cluster("Default VPC / public subnet"):
                with Cluster("Security group\n:8000 from CloudFront prefix list only"):
                    inst = EC2("EC2 c7i.2xlarge\nwhisperx.service")
            bucket = S3("S3 (private)")
            bedrock = Bedrock("Bedrock\nClaude Haiku")
            role = IAM("IAM role")

        viewer >> Edge(label="HTTPS") >> cdn
        cdn >> Edge(label="HTTP + secret header", color="firebrick") >> inst
        inst >> Edge(style="dotted", label="assumes") >> role
        inst >> bucket
        inst >> bedrock


if __name__ == "__main__":
    main()
    deployment()
