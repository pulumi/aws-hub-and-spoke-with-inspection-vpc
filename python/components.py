from dataclasses import dataclass
from typing import Sequence

import pulumi
import pulumi_aws as aws
import pulumi_awsx as awsx


@dataclass
class HubVpcArgs:
    supernet_cidr_block: str
    vpc_cidr_block: str
    tgw_id: pulumi.Input[str]
    spoke_tgw_route_table_id: pulumi.Input[str]
    hub_tgw_route_table_id: pulumi.Input[str]


class HubVpc(pulumi.ComponentResource):
    def __init__(self, name: str, args: HubVpcArgs, opts: pulumi.ResourceOptions = None) -> None:
        super().__init__("awsAdvancedNetworkingWorkshop:index:HubVpc", name, None, opts)

        # So we can reference later in our apply handler:
        self._args = args

        self.vpc = awsx.ec2.Vpc(
            f"{name}-vpc",
            awsx.ec2.VpcArgs(
                cidr_block=args.vpc_cidr_block,
                subnet_specs=[
                    awsx.ec2.SubnetSpecArgs(
                        type=awsx.ec2.SubnetType.PUBLIC,
                        cidr_mask=28,
                    ),
                    awsx.ec2.SubnetSpecArgs(
                        type=awsx.ec2.SubnetType.PRIVATE,
                        cidr_mask=28,
                        name="inspection",
                    ),
                    awsx.ec2.SubnetSpecArgs(
                        type=awsx.ec2.SubnetType.PRIVATE,
                        cidr_mask=28,
                        name="tgw"
                    ),
                ]
            ),
            opts=pulumi.ResourceOptions(
                *(opts or {}),
                parent=self,
            ),
        )

        tgw_subnets = aws.ec2.get_subnets(
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

        inspection_subnets = aws.ec2.get_subnets(
            filters=[
                aws.ec2.GetSubnetFilterArgs(
                    name="tag:Name",
                    values=[f"{name}-vpc-inspection-*"],
                ),
                aws.ec2.GetSubnetFilterArgs(
                    name="vpc-id",
                    values=[self.vpc.vpc_id],
                ),
            ],
        )

        pulumi.Output.all(self.vpc.vpc_id, inspection_subnets.ids, tgw_subnets.ids).apply(
            lambda args: self._add_tgw_resources(args[0], args[1], args[2]))

    def _add_tgw_resources(
        self,
        vpc_id: str,
        inspection_subnet_ids: Sequence[str],
        tgw_subnet_ids: Sequence[str],
    ):
        self.tgw_attachment = aws.ec2transitgateway.VpcAttachment(
            f"{self._name}-tgw-vpc-attachment",
            aws.ec2transitgateway.VpcAttachmentArgs(
                transit_gateway_id=self._args.tgw_id,
                subnet_ids=tgw_subnet_ids,
                vpc_id=vpc_id,
                transit_gateway_default_route_table_association=False,
                transit_gateway_default_route_table_propagation=False,
                appliance_mode_support="enable",
            ),
            # We can only have one attachment per VPC, so we need to tell Pulumi
            # explicitly to delete the old one before creating a new one:
            pulumi.ResourceOptions(
                delete_before_replace=True,
                depends_on=[self.vpc],
                parent=self,
            )
        )

        aws.ec2transitgateway.Route(
            f"{self._name}-default-spoke-to-inspection",
            aws.ec2transitgateway.RouteArgs(
                destination_cidr_block="0.0.0.0/0",
                transit_gateway_attachment_id=self.tgw_attachment.id,
                transit_gateway_route_table_id=self._args.spoke_tgw_route_table_id,
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
            )
        )

        aws.ec2transitgateway.RouteTableAssociation(
            f"{self._name}-tgw-route-table-assoc",
            aws.ec2transitgateway.RouteTableAssociationArgs(
                transit_gateway_attachment_id=self.tgw_attachment.id,
                transit_gateway_route_table_id=self._args.hub_tgw_route_table_id,
            ),
            pulumi.ResourceOptions(
                parent=self
            ),
        )

        for subnet_id in inspection_subnet_ids:
            route_table = aws.ec2.get_route_table(
                subnet_id=subnet_id
            )

            aws.ec2.Route(
                f"{self._name}-tgw-route-{subnet_id}",
                aws.ec2.RouteArgs(
                    route_table_id=route_table.id,
                    destination_cidr_block=self._args.supernet_cidr_block,
                    transit_gateway_id=self._args.tgw_id,
                ),
                pulumi.ResourceOptions(
                    depends_on=[self.tgw_attachment],
                    parent=self,
                ),
            )

        self.register_outputs({
            "vpc": self.vpc,
            "tgw_attachment": self.tgw_attachment,
        })


@dataclass
class SpokeVpcArgs:
    vpc_cidr_block: str
    tgw_id: pulumi.Input[str]
    tgw_route_table_id: pulumi.Input[str]


class SpokeVpc(pulumi.ComponentResource):
    def __init__(self, name: str, args: HubVpcArgs, opts: pulumi.ResourceOptions = None) -> None:
        super().__init__("awsAdvancedNetworkingWorkshop:index:SpokeVpc", name, None, opts)

        # Spoke VPCs don't have a need for public subnets because all egress to the
        # internet will flow through the TGW and out the inspection VPC.
        self.vpc = awsx.ec2.Vpc(
            f"{name}-vpc",
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
                )
            )
        )

        # Using get_subnets rather than vpc.isolated_subnet_ids because it's more
        # stable (in case we change the subnet type above) and descriptive:
        private_subnets = aws.ec2.get_subnets(
            filters=[
                aws.ec2.GetSubnetFilterArgs(
                    name="tag:Name",
                    values=[f"{name}-vpc-private-*"],
                ),
                aws.ec2.GetSubnetFilterArgs(
                    name="vpc-id",
                    values=[self.vpc.vpc_id],
                ),
            ]
        )

        tgw_subnets = aws.ec2.get_subnets(
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

        pulumi.Output.all(self.vpc.vpc_id, private_subnets.ids, tgw_subnets.ids).apply(
            lambda args: self._create_tgw_attachment_resources(args[0], args[1], args[2], args.tgw_id, args.tgw_route_table_id, name))

    def _create_tgw_attachment_resources(
        self,
        vpc_id: str,
        private_subnet_ids: Sequence[str],
        tgw_subnet_ids: Sequence[str],
        tgw_id: pulumi.Input[str],
        tgw_route_table_id: str,
        name: str,
    ):
        tgw_attachment = aws.ec2transitgateway.VpcAttachment(
            f"{name}-tgw-vpc-attachment",
            aws.ec2transitgateway.VpcAttachmentArgs(
                transit_gateway_id=tgw_id,
                subnet_ids=tgw_subnet_ids,
                vpc_id=vpc_id,
                transit_gateway_default_route_table_association=False,
                transit_gateway_default_route_table_propagation=False,
                tags={
                    f"{name}-tgw-vpc-attachment",
                },
            ),
            # We can only have one attachment per VPC, so we need to tell Pulumi
            # explicitly to delete the old one before creating a new one:
            pulumi.ResourceOptions(
                delete_before_replace=True,
                parent=self,
            )
        )

        aws.ec2transitgateway.RouteTableAssociation(
            f"{name}-tgw-route-table-assoc",
            aws.ec2transitgateway.RouteTableAssociationArgs(
                transit_gateway_attachment_id=tgw_attachment.id,
                transit_gateway_route_table_id=tgw_route_table_id,
            ),
            pulumi.ResourceOptions(
                parent=self,
            )
        )

        aws.ec2transitgateway.RouteTablePropagation(
            f"{name}-tgw-route-table-propagation",
            aws.ec2transitgateway.RouteTablePropagationArgs(
                transit_gateway_attachment_id=tgw_attachment.id,
                transit_gateway_route_table_id=tgw_route_table_id,
            ),
            pulumi.ResourceOptions(
                parent=self,
            )
        )

        for subnet_id in private_subnet_ids:
            route_table = aws.ec2.get_route_table(
                subnet_id=subnet_id
            )

            # Direct egress for anything outside this VPC to the Transit Gateway:
            aws.ec2.Route(
                f"spoke{name}-tgw-route-{subnet_id}",
                aws.ec2.RouteArgs(
                    route_table_id=route_table.id,
                    destination_cidr_block="0.0.0.0/0",
                    transit_gateway_id=tgw_id,
                ),
                pulumi.ResourceOptions(
                    depends_on=[tgw_attachment],
                    parent=self,
                ),
            )


@ dataclass
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
                vpc_id=args.vpc_id,
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
                subnet_id=args.instance_subnet_id,
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
                destination=args.igw_id,
                destination_port=80,
                source=instance.id,
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
                destination=args.igw_id,
                destination_port=443,
                source=instance.id,
            ),
            opts=pulumi.ResourceOptions(
                parent=self
            ),
        )

        self.https_analysis = aws.ec2.NetworkInsightsAnalysis(
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

        self.register_outputs({
            "http_analysis": self.http_analysis,
            "https_analysis": self.https_analysis,
        })
