from dataclasses import dataclass
from typing import Sequence

import json

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
                        description="Allow everything",
                        protocol="-1",
                        from_port=0,
                        to_port=0
                    ),
                    # aws.ec2.SecurityGroupEgressArgs(
                    #     cidr_blocks=["0.0.0.0/0"],
                    #     description="Allow outbound HTTP to any destination",
                    #     from_port=80,
                    #     to_port=80,
                    #     protocol="tcp",
                    # ),
                    # aws.ec2.SecurityGroupEgressArgs(
                    #     cidr_blocks=["0.0.0.0/0"],
                    #     description="Allow outbound HTTPs to any destination",
                    #     from_port=443,
                    #     to_port=443,
                    #     protocol="tcp",
                    # ),
                ]
            ),
            opts=pulumi.ResourceOptions(
                parent=self
            ),
        )

        ec2_role = aws.iam.Role(
            f"{name}-instance-role",
            aws.iam.RoleArgs(
                assume_role_policy=json.dumps({
                    "Version": "2012-10-17",
                    "Statement": {
                        "Effect": "Allow",
                        "Principal": {
                            "Service": "ec2.amazonaws.com",
                        },
                        "Action": "sts:AssumeRole",
                    },
                })
            )
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-role-policy-attachment",
            aws.iam.RolePolicyAttachmentArgs(
                role=ec2_role.name,
                policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
            )
        )

        instance_profile = aws.iam.InstanceProfile(
            f"{name}-instance-profile",
            aws.iam.InstanceProfileArgs(
                role=ec2_role.name,
            )
        )

        amazon_linux_2 = aws.ec2.get_ami(
            most_recent=True,
            owners=["amazon"],
            filters=[
                aws.ec2.GetAmiFilterArgs(
                    name="name",
                    values=["amzn2-ami-hvm-*-x86_64-gp2"],
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
                instance_type="t3.micro",
                vpc_security_group_ids=[sg.id],
                subnet_id=args.spoke_instance_subnet_id,
                tags={
                    "Name": f"{name}-instance",
                },
                iam_instance_profile=instance_profile.name,
            ),
            opts=pulumi.ResourceOptions(
                parent=self
            ),
        )

        http_path = aws.ec2.NetworkInsightsPath(
            f"{name}-to-hub-igw-path-http",
            aws.ec2.NetworkInsightsPathArgs(
                destination=args.hub_igw_id,
                destination_port=80,
                source=instance.id,
                protocol="tcp",
                tags={
                    "Name": f"{name}-to-hub-igw-path-http"
                }
            ),
            opts=pulumi.ResourceOptions(
                parent=self
            ),
        )

        aws.ec2.NetworkInsightsPath(
            f"hub-igw-to-{name}-path-http",
            aws.ec2.NetworkInsightsPathArgs(
                source=args.hub_igw_id,
                destination=instance.id,
                destination_port=80,
                protocol="tcp",
                tags={
                    "Name": f"hub-igw-to-{name}-path-http"
                }
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


@dataclass
class SpokeVpcArgs:
    vpc_cidr_block: str
    tgw_id: pulumi.Input[str]
    tgw_route_table_id: pulumi.Input[str]


class SpokeVpc(pulumi.ComponentResource):
    def __init__(self, name: str, args: SpokeVpcArgs, opts: pulumi.ResourceOptions = None) -> None:
        super().__init__("awsAdvancedNetworkingWorkshop:index:SpokeVpc", name, None, opts)

        self._name = name
        self._args = args

        # Spoke VPCs don't have a need for public subnets because all egress to the
        # internet will flow through the TGW and out the inspection VPC.
        self.vpc = awsx.ec2.Vpc(
            f"{self._name}-vpc",
            awsx.ec2.VpcArgs(
                cidr_block=args.vpc_cidr_block,
                subnet_specs=[
                    # We specify ISOLATED as the following subnet type because we do
                    # have NAT gateways to which to route traffic. (A route to a NAT
                    # gateway is what distinguishes PRIVATE from ISOLATED.)
                    #
                    # We will add a route for egress to the internet later on that
                    # goes to the TGW. In practice, these subnets will behave like
                    # private subnets - it's just that the NAT Gateway is in our hub
                    # VPC as opposed to this VPC.
                    awsx.ec2.SubnetSpecArgs(
                        name="private",
                        cidr_mask=28,
                        type=awsx.ec2.SubnetType.ISOLATED,
                    ),
                    awsx.ec2.SubnetSpecArgs(
                        name="tgw",
                        cidr_mask=28,
                        type=awsx.ec2.SubnetType.ISOLATED,
                    ),
                ],
                nat_gateways=awsx.ec2.NatGatewayConfigurationArgs(
                    strategy=awsx.ec2.NatGatewayStrategy.NONE,
                ),
                enable_dns_hostnames=True,
                enable_dns_support=True,
            )
        )

        tgw_subnets = aws.ec2.get_subnets_output(
            filters=[
                aws.ec2.GetSubnetFilterArgs(
                    name="tag:Name",
                    values=[f"{name}-vpc-tgw-*"],
                ),
                aws.ec2.GetSubnetFilterArgs(
                    name="vpc-id",
                    values=[self.vpc.vpc_id],
                ),
            ]
        )

        tgw_subnets = aws.ec2.get_subnets_output(
            filters=[
                aws.ec2.GetSubnetFilterArgs(
                    name="tag:Name",
                    values=[f"{self._name}-vpc-tgw-*"],
                ),
                aws.ec2.GetSubnetFilterArgs(
                    name="vpc-id",
                    values=[self.vpc.vpc_id],
                ),
            ]
        )

        self.tgw_attachment = aws.ec2transitgateway.VpcAttachment(
            f"{name}-tgw-vpc-attachment",
            aws.ec2transitgateway.VpcAttachmentArgs(
                transit_gateway_id=args.tgw_id,
                subnet_ids=tgw_subnets.apply(lambda x: x.ids),
                vpc_id=self.vpc.vpc_id,
                transit_gateway_default_route_table_association=False,
                transit_gateway_default_route_table_propagation=False,
                tags={
                    "Name": f"{name}",
                },
            ),
            # We can only have one attachment per VPC, so we need to tell Pulumi
            # explicitly to delete the old one before creating a new one:
            pulumi.ResourceOptions(
                delete_before_replace=True,
                depends_on=[self.vpc],
                parent=self,
            )
        )

        aws.ec2transitgateway.RouteTableAssociation(
            f"{name}-tgw-route-table-assoc",
            aws.ec2transitgateway.RouteTableAssociationArgs(
                transit_gateway_attachment_id=self.tgw_attachment.id,
                transit_gateway_route_table_id=self._args.tgw_route_table_id,
            ),
            pulumi.ResourceOptions(
                parent=self,
            )
        )

        aws.ec2transitgateway.RouteTablePropagation(
            f"{name}-tgw-route-table-propagation",
            aws.ec2transitgateway.RouteTablePropagationArgs(
                transit_gateway_attachment_id=self.tgw_attachment.id,
                transit_gateway_route_table_id=self._args.tgw_route_table_id,
            ),
            pulumi.ResourceOptions(
                parent=self,
            ),
        )

        # Using get_subnets rather than vpc.isolated_subnet_ids because it's more
        # stable (in case we change the subnet type above) and descriptive:
        private_subnets = aws.ec2.get_subnets_output(
            filters=[
                aws.ec2.GetSubnetFilterArgs(
                    name="tag:Name",
                    values=[f"{self._name}-vpc-private-*"],
                ),
                aws.ec2.GetSubnetFilterArgs(
                    name="vpc-id",
                    values=[self.vpc.vpc_id],
                ),
            ]
        )
        self.workload_subnet_ids = private_subnets.ids

        private_subnets.apply(lambda x: self._create_vpc_endpoints(x.ids))
        private_subnets.apply(lambda x: self._create_routes(x.ids))

    def _create_vpc_endpoints(
        self,
        subnet_ids: Sequence[str]
    ):
        # TODO: Once we get this working, restrict these to inbound HTTPS
        vpc_endpoint_sg = aws.ec2.SecurityGroup(
            f"{self._name}-vpc-endpoint-sg",
            aws.ec2.SecurityGroupArgs(
                vpc_id=self.vpc.vpc_id,
                ingress=[
                    aws.ec2.SecurityGroupEgressArgs(
                        cidr_blocks=["0.0.0.0/0"],
                        description="Allow everything",
                        protocol="-1",
                        from_port=0,
                        to_port=0
                    ),
                ],
                egress=[
                    aws.ec2.SecurityGroupEgressArgs(
                        cidr_blocks=["0.0.0.0/0"],
                        description="Allow everything",
                        protocol="-1",
                        from_port=0,
                        to_port=0
                    ),
                ]
            )
        )

        for service in ["ec2messages", "ssmmessages", "ssm"]:
            aws.ec2.VpcEndpoint(
                f"{self._name}-endpoint-{service}",
                aws.ec2.VpcEndpointArgs(
                    vpc_id=self.vpc.vpc_id,
                    service_name=f"com.amazonaws.{aws.config.region}.{service}",
                    private_dns_enabled=True,
                    security_group_ids=[vpc_endpoint_sg.id],
                    vpc_endpoint_type="Interface",
                    tags={
                        "Name": f"{self._name}-{service}"
                    },
                    subnet_ids=subnet_ids
                )
            )

    def _create_routes(
        self,
        private_subnet_ids: Sequence[str],
    ):

        for subnet_id in private_subnet_ids:
            route_table = aws.ec2.get_route_table(
                subnet_id=subnet_id,
            )

            # Direct egress for anything outside this VPC to the Transit Gateway:
            aws.ec2.Route(
                f"spoke{self._name}-tgw-route-{subnet_id}",
                aws.ec2.RouteArgs(
                    route_table_id=route_table.id,
                    destination_cidr_block="0.0.0.0/0",
                    transit_gateway_id=self._args.tgw_id,
                ),
                pulumi.ResourceOptions(
                    depends_on=[self.tgw_attachment],
                    parent=self,
                ),
            )

        self.register_outputs({
            "vpc": self.vpc,
            "workload_subnet_ids": self.workload_subnet_ids
        })
