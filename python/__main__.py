"""A Python Pulumi program"""

from typing import Sequence

import pulumi
import pulumi_aws as aws
import pulumi_awsx as awsx

from components import HubVpc, HubVpcArgs, SpokeVpc, SpokeVpcArgs

project = pulumi.get_project()

# TODO:
# - Tags

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
            "Name": "Post-Inspection Route Table",
        }
    ),
    # Adding the TGW as the parent makes the display of `pulumi up` a little
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
            "Name": "spoke-tgw-route-table",
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


# def add_tgw_resources(
#     vpc_id: str,
#     inspection_subnet_ids: Sequence[str],
#     tgw_subnet_ids: Sequence[str]
# ):
#     tgw_attachment = aws.ec2transitgateway.VpcAttachment(
#         "inspection-gw-vpc-attachment",
#         aws.ec2transitgateway.VpcAttachmentArgs(
#             transit_gateway_id=tgw.id,
#             subnet_ids=tgw_subnet_ids,
#             vpc_id=vpc_id,
#             transit_gateway_default_route_table_association=False,
#             transit_gateway_default_route_table_propagation=False,
#             appliance_mode_support="enable",
#         ),
#         # We can only have one attachment per VPC, so we need to tell Pulumi
#         # explicitly to delete the old one before creating a new one:
#         pulumi.ResourceOptions(
#             delete_before_replace=True,
#             depends_on=[inspection_vpc],
#         )
#     )

#     aws.ec2transitgateway.Route(
#         "default-spoke-to-inspection",
#         aws.ec2transitgateway.RouteArgs(
#             destination_cidr_block="0.0.0.0/0",
#             transit_gateway_attachment_id=tgw_attachment.id,
#             transit_gateway_route_table_id=spoke_tgw_route_table.id,
#         ),
#     )

#     aws.ec2transitgateway.RouteTableAssociation(
#         "inspection-tgw-route-table-assoc",
#         aws.ec2transitgateway.RouteTableAssociationArgs(
#             transit_gateway_attachment_id=tgw_attachment.id,
#             transit_gateway_route_table_id=inspection_tgw_route_table.id,
#         )
#     )

#     for subnet_id in inspection_subnet_ids:
#         route_table = aws.ec2.get_route_table(
#             subnet_id=subnet_id
#         )

#         aws.ec2.Route(
#             f"inspection-tgw-route-{subnet_id}",
#             aws.ec2.RouteArgs(
#                 route_table_id=route_table.id,
#                 destination_cidr_block=hub_and_spoke_supernet,
#                 transit_gateway_id=tgw.id,
#             ),
#             pulumi.ResourceOptions(
#                 depends_on=[tgw_attachment]
#             ),
#         )


# inspection_vpc_inspection_subnets = aws.ec2.get_subnets(
#     filters=[
#         aws.ec2.GetSubnetFilterArgs(
#             name="tag:Name",
#             values=["inspection-vpc-inspection-*"],
#         ),
#         aws.ec2.GetSubnetFilterArgs(
#             name="vpc-id",
#             values=[inspection_vpc.vpc_id],
#         ),
#     ],
# )

# inspection_vpc_tgw_subnets = aws.ec2.get_subnets(
#     filters=[
#         aws.ec2.GetSubnetFilterArgs(
#             name="tag:Name",
#             values=["inspection-vpc-tgw-*"],
#         ),
#         aws.ec2.GetSubnetFilterArgs(
#             name="vpc-id",
#             values=[inspection_vpc.vpc_id],
#         ),
#     ]
# )

# pulumi.Output.all(inspection_vpc.vpc_id, inspection_vpc_inspection_subnets.ids, inspection_vpc_tgw_subnets.ids).apply(
#     lambda args: add_tgw_resources(args[0], args[1], args[2]))


# inspection_vpc.internet_gateway.apply(
#     lambda igw: create_spoke_vpc(
#         name="1",
#         cidr_block="10.0.0.0/16",
#         tgw_id=tgw.id,
#         tgw_route_table_id=spoke_tgw_route_table.id,
#         hub_igw_id=igw.id,
#     )
# )

# create_spoke_vpc(
#     name="2",
#     cidr_block="10.0.0.0/16"
# )
