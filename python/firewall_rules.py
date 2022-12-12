import pulumi_aws as aws
import pulumi as pulumi


def create_firewall_policy() -> pulumi.Output[str]:
    allow_amazon = aws.networkfirewall.RuleGroup(
        "allow-amazon",
        aws.networkfirewall.RuleGroupArgs(
            capacity=100,
            name="allow-amazon",
            type="STATEFUL",
            rule_group=aws.networkfirewall.RuleGroupRuleGroupArgs(
                rules_source=aws.networkfirewall.RuleGroupRuleGroupRulesSourceArgs(
                    rules_string='pass tcp any any <> $EXTERNAL_NET 443 (msg:"Allowing TCP in port 443"; flow:not_established; sid:892123; rev:1;)\n' +
                    'pass tls any any -> $EXTERNAL_NET 443 (tls.sni; dotprefix; content:".amazon.com"; endswith; msg:"Allowing .amazon.com HTTPS requests"; sid:892125; rev:1;)'
                ),
                stateful_rule_options={
                    "rule_order": "STRICT_ORDER",
                },
            )
        )
    )

    policy = aws.networkfirewall.FirewallPolicy(
        "firewall-policy",
        aws.networkfirewall.FirewallPolicyArgs(
            firewall_policy=aws.networkfirewall.FirewallPolicyFirewallPolicyArgs(
                stateless_default_actions=["aws:forward_to_sfe"],
                stateless_fragment_default_actions=["aws:forward_to_sfe"],
                stateful_default_actions=[
                    "aws:drop_strict", "aws:alert_strict"],
                stateful_engine_options={
                    "rule_order": "STRICT_ORDER"
                },
                stateful_rule_group_references=[
                    aws.networkfirewall.FirewallPolicyFirewallPolicyStatelessRuleGroupReferenceArgs(
                        priority=10,
                        resource_arn=allow_amazon.arn,
                    )
                ]
            )
        )
    )

    return policy.arn
