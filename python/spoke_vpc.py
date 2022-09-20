from dataclasses import dataclass
from typing import Sequence

import pulumi
import pulumi_aws as aws
import pulumi_awsx as awsx


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
                )
            )
        )

        # Using get_subnets rather than vpc.isolated_subnet_ids because it's more
        # stable (in case we change the subnet type above) and descriptive:
        private_subnets = aws.ec2.get_subnets(
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

        tgw_subnets = aws.ec2.get_subnets(
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

        pulumi.Output.all(self.vpc.vpc_id, private_subnets.ids, tgw_subnets.ids).apply(
            lambda args: self._create_tgw_attachment_resources(args[0], args[1], args[2]))

    def _create_tgw_attachment_resources(
        self,
        vpc_id: str,
        private_subnet_ids: Sequence[str],
        tgw_subnet_ids: Sequence[str],
    ):
        tgw_attachment = aws.ec2transitgateway.VpcAttachment(
            f"{self._name}-tgw-vpc-attachment",
            aws.ec2transitgateway.VpcAttachmentArgs(
                transit_gateway_id=self._args.tgw_id,
                subnet_ids=tgw_subnet_ids,
                vpc_id=vpc_id,
                transit_gateway_default_route_table_association=False,
                transit_gateway_default_route_table_propagation=False,
                tags={
                    "Name": f"{self._name}-tgw-vpc-attachment",
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
            f"{self._name}-tgw-route-table-assoc",
            aws.ec2transitgateway.RouteTableAssociationArgs(
                transit_gateway_attachment_id=tgw_attachment.id,
                transit_gateway_route_table_id=self._args.tgw_route_table_id,
            ),
            pulumi.ResourceOptions(
                parent=self,
            )
        )

        aws.ec2transitgateway.RouteTablePropagation(
            f"{self._name}-tgw-route-table-propagation",
            aws.ec2transitgateway.RouteTablePropagationArgs(
                transit_gateway_attachment_id=tgw_attachment.id,
                transit_gateway_route_table_id=self._args.tgw_route_table_id,
            ),
            pulumi.ResourceOptions(
                parent=self,
            ),
        )

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
                    depends_on=[tgw_attachment],
                    parent=self,
                ),
            )

        self.register_outputs({
            "vpc": self.vpc,
            "workload_subnet_ids": self.workload_subnet_ids
        })
