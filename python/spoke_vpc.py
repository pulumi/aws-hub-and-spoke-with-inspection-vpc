from typing import Sequence

import pulumi
import pulumi_aws as aws
import pulumi_awsx as awsx


def create_spoke_vpc(
    name: str,
    cidr_block: str,
    tgw_id,
    tgw_route_table_id,
    hub_igw_id,
):
    vpc_name = f"spoke-vpc-{name}"

    # Spoke VPCs don't have a need for public subnets because all egress to the
    # internet will flow through the TGW and out the inspection VPC.
    spoke_vpc = awsx.ec2.Vpc(
        vpc_name,
        awsx.ec2.VpcArgs(
            cidr_block=cidr_block,
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
                values=[f"{vpc_name}-private-*"],
            ),
            aws.ec2.GetSubnetFilterArgs(
                name="vpc-id",
                values=[spoke_vpc.vpc_id],
            ),
        ]
    )

    sg = aws.ec2.SecurityGroup(
        f"instance-sg-{name}",
        aws.ec2.SecurityGroupArgs(
            description="Allow outbound HTTP/S to any destination",
            vpc_id=spoke_vpc.vpc_id,
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
        )
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
        f"instance-{name}",
        aws.ec2.InstanceArgs(
            ami=amazon_linux_2.id,
            instance_type="t2.micro",
            vpc_security_group_ids=[sg.id],
            subnet_id=private_subnets.ids[0],
            tags={
                "Name": f"spoke-vpc-instance-{name}",
            }
        ),
    )

    # # Create a Network Analyzer path so we can verify (positive or negatively)
    # # our EC2 instance's connectivity:
    for port in [80, 443]:
        aws.ec2.NetworkInsightsPath(
            f"instance-to-internet-{port}",
            aws.ec2.NetworkInsightsPathArgs(
                destination=hub_igw_id,
                destination_port=port,
                source=instance.id,
                protocol="tcp",
                tags={
                    "Name": f"instance-to-internet-{port}"
                }
            ),
        )

    tgw_subnets = aws.ec2.get_subnets(
        filters=[
            aws.ec2.GetSubnetFilterArgs(
                name="tag:Name",
                values=[f"{vpc_name}-tgw-*"],
            ),
            aws.ec2.GetSubnetFilterArgs(
                name="vpc-id",
                values=[spoke_vpc.vpc_id],
            ),
        ]
    )

    pulumi.Output.all(spoke_vpc.vpc_id, private_subnets.ids, tgw_subnets.ids).apply(
        lambda args: create_tgw_attachment_resources(args[0], args[1], args[2], tgw_id, tgw_route_table_id, name))


def create_tgw_attachment_resources(
    vpc_id: str,
    private_subnet_ids: Sequence[str],
    tgw_subnet_ids: Sequence[str],
    tgw_id: str,
    tgw_route_table_id: str,
    name: str,
):
    tgw_attachment = aws.ec2transitgateway.VpcAttachment(
        f"spoke-tgw-vpc-attachment-{name}",
        aws.ec2transitgateway.VpcAttachmentArgs(
            transit_gateway_id=tgw_id,
            subnet_ids=tgw_subnet_ids,
            vpc_id=vpc_id,
            transit_gateway_default_route_table_association=False,
            transit_gateway_default_route_table_propagation=False,
            tags={
                "Name": f"spoke-vpc-{name}",
            },
        ),
        # We can only have one attachment per VPC, so we need to tell Pulumi
        # explicitly to delete the old one before creating a new one:
        pulumi.ResourceOptions(
            delete_before_replace=True,
        )
    )

    aws.ec2transitgateway.RouteTableAssociation(
        f"spoke-tgw-route-table-assoc-{name}",
        aws.ec2transitgateway.RouteTableAssociationArgs(
            transit_gateway_attachment_id=tgw_attachment.id,
            transit_gateway_route_table_id=tgw_route_table_id,
        )
    )

    aws.ec2transitgateway.RouteTablePropagation(
        f"spoke-tgw-route-table-propagation-{name}",
        aws.ec2transitgateway.RouteTablePropagationArgs(
            transit_gateway_attachment_id=tgw_attachment.id,
            transit_gateway_route_table_id=tgw_route_table_id,
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
                depends_on=[tgw_attachment]
            ),
        )
