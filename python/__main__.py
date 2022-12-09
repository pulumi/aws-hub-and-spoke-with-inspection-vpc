"""A Python Pulumi program"""

import pulumi
import pulumi_aws as aws

from hub import HubVpc, HubVpcArgs
from spoke import SpokeVpc, SpokeVpcArgs, SpokeVerification, SpokeVerificationArgs

project = pulumi.get_project()

config = pulumi.Config()
hub_and_spoke_supernet = config.require("hub-and-spoke-supernet")

tgw = aws.ec2transitgateway.TransitGateway(
    "tgw",
    aws.ec2transitgateway.TransitGatewayArgs(
        description=f"Transit Gateway - {project}",
        default_route_table_association="disable",
        default_route_table_propagation="disable",
        tags={
            "Name": "Pulumi"
        }
    )
)


inspection_tgw_route_table = aws.ec2transitgateway.RouteTable(
    "post-inspection-tgw-route-table",
    aws.ec2transitgateway.RouteTableArgs(
        transit_gateway_id=tgw.id,
        tags={
            "Name": "post-inspection",
        }
    ),
    # Adding the TGW as the parent makes the output of `pulumi up` a little
    # easier to understand as it groups these resources visually under the TGW
    # on which they depend.
    opts=pulumi.ResourceOptions(
        parent=tgw,
    ),
)


spoke_tgw_route_table = aws.ec2transitgateway.RouteTable(
    "spoke-tgw-route-table",
    aws.ec2transitgateway.RouteTableArgs(
        transit_gateway_id=tgw.id,
        tags={
            "Name": "spoke-tgw",
        }
    ),
    opts=pulumi.ResourceOptions(
        parent=tgw,
    ),
)

hub_tgw_route_table = aws.ec2transitgateway.RouteTable(
    "hub-tgw-route-table",
    aws.ec2transitgateway.RouteTableArgs(
        transit_gateway_id=tgw.id,
        tags={
            "Name": "hub-tgw-route-table",
        }
    ),
    opts=pulumi.ResourceOptions(
        parent=tgw,
    ),
)

hub_vpc = HubVpc(
    "hub",
    HubVpcArgs(
        supernet_cidr_block=hub_and_spoke_supernet,
        vpc_cidr_block="10.129.0.0/24",
        tgw_id=tgw.id,
        hub_tgw_route_table_id=hub_tgw_route_table.id,
        spoke_tgw_route_table_id=spoke_tgw_route_table.id,
    )
)

spoke_vpc = SpokeVpc(
    "spoke1",
    SpokeVpcArgs(
        vpc_cidr_block="10.0.0.0/16",
        tgw_id=tgw.id,
        tgw_route_table_id=spoke_tgw_route_table.id,
    ),
)

aws.ec2transitgateway.RouteTablePropagation(
    "hub-to-spoke1",
    aws.ec2transitgateway.RouteTablePropagationArgs(
        transit_gateway_attachment_id=spoke_vpc.tgw_attachment.id,
        transit_gateway_route_table_id=hub_tgw_route_table.id,
    )
)

spoke_1_verification = SpokeVerification(
    "spoke1verification",
    args=SpokeVerificationArgs(
        hub_igw_id=hub_vpc.vpc.internet_gateway.id,
        spoke_instance_subnet_id=spoke_vpc.workload_subnet_ids[0],
        spoke_vpc_id=spoke_vpc.vpc.vpc_id,
    ),
)

pulumi.export("nat-gateway-eip", hub_vpc.vpc.eips[0].public_ip)
