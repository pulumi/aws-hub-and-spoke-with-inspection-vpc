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
