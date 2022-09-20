from dataclasses import dataclass
from typing import Sequence

import pulumi
import pulumi_aws as aws
import pulumi_awsx as awsx


@dataclass
class SpokeVerificationArgs:
    spoke_vpc_id: pulumi.Input[str]
    spoke_instance_subnet_id: str
    hub_igw_id: pulumi.Input[str]


# Comprises an EC2 instance, security group, and network reachability analyzer
# resources to verify that the spoke VPC can route to the hub VPC's IGW
class SpokeVerification(pulumi.ComponentResource):
    def __init__(self, name: str, args: SpokeVerificationArgs, opts: pulumi.ResourceOptions = None) -> None:
        super().__init__("awsAdvancedNetworkingWorkshop:index:SpokeVerification", name, None, opts)

        sg = aws.ec2.SecurityGroup(
            f"{name}-instance-sg",
            aws.ec2.SecurityGroupArgs(
                description="Allow outbound HTTP/S to any destination",
                vpc_id=args.spoke_vpc_id,
                egress=[
                    aws.ec2.SecurityGroupEgressArgs(
                        cidr_blocks=["0.0.0.0/0"],
                        description="Allow outbound HTTP to any destination",
                        from_port=80,
                        to_port=80,
                        protocol="tcp",
                    ),
                    aws.ec2.SecurityGroupEgressArgs(
                        cidr_blocks=["0.0.0.0/0"],
                        description="Allow outbound HTTPs to any destination",
                        from_port=443,
                        to_port=443,
                        protocol="tcp",
                    ),
                ]
            ),
            opts=pulumi.ResourceOptions(
                parent=self
            ),
        )

        amazon_linux_2 = aws.ec2.get_ami(
            most_recent=True,
            owners=["amazon"],
            filters=[
                aws.ec2.GetAmiFilterArgs(
                    name="name",
                    values=["amzn-ami-hvm-*-x86_64-gp2"],
                ),
                aws.ec2.GetAmiFilterArgs(
                    name="owner-alias",
                    values=["amazon"],
                )
            ],
        )

        instance = aws.ec2.Instance(
            f"{name}-instance",
            aws.ec2.InstanceArgs(
                ami=amazon_linux_2.id,
                instance_type="t2.micro",
                vpc_security_group_ids=[sg.id],
                subnet_id=args.spoke_instance_subnet_id,
                tags={
                    "Name": f"{name}-instance",
                }
            ),
            opts=pulumi.ResourceOptions(
                parent=self
            ),
        )

        http_path = aws.ec2.NetworkInsightsPath(
            f"{name}-network-insights-path-http",
            aws.ec2.NetworkInsightsPathArgs(
                destination=args.hub_igw_id,
                destination_port=80,
                source=instance.id,
                protocol="tcp",
            ),
            opts=pulumi.ResourceOptions(
                parent=self
            ),
        )

        self.http_analysis = aws.ec2.NetworkInsightsAnalysis(
            f"{name}-network-insights-analysis-http",
            aws.ec2.NetworkInsightsAnalysisArgs(
                network_insights_path_id=http_path.id,
                wait_for_completion=False,
            ),
            opts=pulumi.ResourceOptions(
                depends_on=[instance],
                parent=self,
            ),
        )

        aws.ec2.NetworkInsightsPath(
            f"{name}-network-insights-path-https",
            aws.ec2.NetworkInsightsPathArgs(
                destination=args.hub_igw_id,
                destination_port=443,
                source=instance.id,
                protocol="tcp",
            ),
            opts=pulumi.ResourceOptions(
                parent=self
            ),
        )

        self.https_analysis = aws.ec2.NetworkInsightsAnalysis(
            f"{name}-network-insights-analysis-https",
            aws.ec2.NetworkInsightsAnalysisArgs(
                network_insights_path_id=http_path.id,
                wait_for_completion=False,
            ),
            opts=pulumi.ResourceOptions(
                depends_on=[instance],
                parent=self,
            ),
        )

        self.register_outputs({
            "http_analysis": self.http_analysis,
            "https_analysis": self.https_analysis,
        })
