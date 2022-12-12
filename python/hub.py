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
        self._name = name
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
                    # We create these as isolated even because it's easier to change
                    # the route from the NAT to the firewall once we add it.
                    awsx.ec2.SubnetSpecArgs(
                        type=awsx.ec2.SubnetType.ISOLATED,
                        cidr_mask=28,
                        name="tgw"
                    ),
                ],
                # We specify no NAT Gateways because at the time of writing we
                # have overly-strict validation for NAT Gateway strategy. This
                # can be changed to SINGLE when
                # https://github.com/pulumi/pulumi-awsx/issues/966 is resolved.
                nat_gateways=awsx.ec2.NatGatewayConfigurationArgs(
                    strategy=awsx.ec2.NatGatewayStrategy.NONE
                )
            ),
            opts=pulumi.ResourceOptions(
                *(opts or {}),
                parent=self,
            ),
        )

        self.eip = aws.ec2.Eip(
            f"{name}-eip",
            opts=pulumi.ResourceOptions(
                parent=self,
            ),
        )

        nat_gateway = aws.ec2.NatGateway(
            f"{name}-nat-gateway",
            aws.ec2.NatGatewayArgs(
                subnet_id=self.vpc.public_subnet_ids[0],
                allocation_id=self.eip.allocation_id,
            ),
            pulumi.ResourceOptions(
                parent=self
            )
        )

        self.vpc.isolated_subnet_ids.apply(
            lambda ids: self.create_nat_routes(ids, nat_gateway.id))

        self.tgw_attachment = aws.ec2transitgateway.VpcAttachment(
            f"{name}-tgw-vpc-attachment",
            aws.ec2transitgateway.VpcAttachmentArgs(
                transit_gateway_id=self._args.tgw_id,
                subnet_ids=self.vpc.isolated_subnet_ids,
                vpc_id=self.vpc.vpc_id,
                transit_gateway_default_route_table_association=False,
                transit_gateway_default_route_table_propagation=False,
                appliance_mode_support="enable",
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

        # TODO: Fix the name of this route.
        aws.ec2transitgateway.Route(
            f"{name}-default-spoke-to-inspection",
            aws.ec2transitgateway.RouteArgs(
                destination_cidr_block="0.0.0.0/0",
                transit_gateway_attachment_id=self.tgw_attachment.id,
                transit_gateway_route_table_id=args.spoke_tgw_route_table_id,
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
            )
        )

        aws.ec2transitgateway.RouteTableAssociation(
            f"{name}-tgw-route-table-assoc",
            aws.ec2transitgateway.RouteTableAssociationArgs(
                transit_gateway_attachment_id=self.tgw_attachment.id,
                transit_gateway_route_table_id=args.hub_tgw_route_table_id,
            ),
            pulumi.ResourceOptions(
                parent=self
            ),
        )

        self.vpc.public_subnet_ids.apply(
            lambda x: self.create_inbound_routes(x))

        # inspection_subnets.apply(lambda x: self._create_outbound_routes(x.ids))

        self.register_outputs({
            "vpc": self.vpc,
            "eip": self.eip,
            # TODO: Check whether this is being returned before it's actually
            # provisioned and is causing an issue downstream if we try to spin
            # this stack all up at once.
            "tgw_attachment": self.tgw_attachment,
        })

    def create_nat_routes(self, subnet_ids: Sequence[str], nat_gateway_id: pulumi.Output[str]):
        '''Creates routes from the supplied subnet IDs to the NAT Gateway'''
        for subnet_id in subnet_ids:
            route_table = aws.ec2.get_route_table(
                subnet_id=subnet_id
            )

            aws.ec2.Route(
                f"{self._name}-route-{subnet_id}-to-tgw",
                aws.ec2.RouteArgs(
                    route_table_id=route_table.id,
                    destination_cidr_block="0.0.0.0/0",
                    nat_gateway_id=nat_gateway_id,
                ),
                pulumi.ResourceOptions(
                    parent=self,
                ),
            )

    def create_inbound_routes(self, subnet_ids: Sequence[str]):
        '''Creates routes for the supernet (a CIDR block that encompasses all
        spoke VPCs) from the public subnets in the hub VPC (where the NAT
        Gateways for centralized egress live) to the TGW'''
        for subnet_id in subnet_ids:
            route_table = aws.ec2.get_route_table(
                subnet_id=subnet_id
            )

            aws.ec2.Route(
                f"{self._name}-route-{subnet_id}-to-tgw",
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
