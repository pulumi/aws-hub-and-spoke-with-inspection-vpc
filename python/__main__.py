"""A Python Pulumi program"""

import pulumi
import pulumi_aws as aws

from hub_vpc import HubVpc, HubVpcArgs
from spoke_vpc import SpokeVpc, SpokeVpcArgs
from spoke_verification import SpokeVerification, SpokeVerificationArgs

project = pulumi.get_project()

hub_and_spoke_supernet = pulumi.Config().require("hub-and-spoke-supernet")

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


def create_verification(
    hub_igw,
    spoke_vpc_workload_subnet_ids,
):
    return SpokeVerification(
        "spoke1verification",
        args=SpokeVerificationArgs(
            hub_igw_id=hub_igw.id,
            spoke_instance_subnet_id=spoke_vpc_workload_subnet_ids[0],
            spoke_vpc_id=spoke_vpc.vpc.vpc_id,
        ),
    )


spoke_1_verification = pulumi.Output.all(hub_vpc.vpc.internet_gateway, spoke_vpc.workload_subnet_ids).apply(
    lambda args: create_verification(args[0], args[1]))

# If these are uncommented, spoke_vpc_workload_subnet_ids will be empty and
# preview will throw an exception because the index [0] is out of range. I
# believe this is because inside SpokeVpc, we're evaluating get_subnets before
# the VPC is created, but I cannot figure out a way to execute get_subnets only
# after a VPC component is fully initialized.
# pulumi.export("spoke_1_http_path_analysis",
#               spoke_1_verification.http_analysis)
# pulumi.export("spoke_1_https_path_analysis",
#               spoke_1_verification.https_analysis)
