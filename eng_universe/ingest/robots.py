import time
from dataclasses import dataclass
import math
import re
from urllib.parse import urlparse, urlsplit
from urllib.robotparser import RobotFileParser

import aiohttp
import redis.asyncio as redis

from eng_universe.config import Settings


@dataclass
class RobotsRules:
    """Stores parsed robots policy data for one domain."""

    domain: str
    crawl_delay_s: int
    request_rate_s: int
    allowed: bool
    fetched_at: int
    text: str


def robots_cache_key(domain: str, scheme: str = "https") -> str:
    if scheme not in {"http", "https"}:
        raise ValueError("robots scheme must be http or https")
    if scheme == "http":
        return f"{Settings.robots_key_prefix}http:{domain}"
    return f"{Settings.robots_key_prefix}{domain}"


def robots_next_allowed_key(domain: str) -> str:
    return f"{Settings.robots_next_allowed_prefix}{domain}"


def parse_domain(url: str) -> str:
    return urlparse(url).netloc


async def fetch_robots_txt(
    session: aiohttp.ClientSession,
    domain: str,
    scheme: str = "https",
) -> str:
    if scheme not in {"http", "https"}:
        raise ValueError("robots scheme must be http or https")
    robots_url = f"{scheme}://{domain}/robots.txt"
    async with session.get(robots_url, timeout=Settings.request_timeout_s) as response:
        if response.status >= 400:
            return ""
        return await response.text()


def _parse_request_rate_value(value: str) -> int:
    match = re.match(r"^\s*(\d+)\s*/\s*([\d.]+)\s*([smhd])?\s*$", value)
    if not match:
        return 0
    requests = int(match.group(1))
    if requests <= 0:
        return 0
    window = float(match.group(2))
    unit = match.group(3) or "s"
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 1)
    window_s = window * multiplier
    if window_s <= 0:
        return 0
    return int(math.ceil(window_s / requests))


def _extract_request_rate(robots_txt: str, user_agent: str) -> int:
    user_agent = user_agent.lower()
    groups: list[tuple[list[str], list[str]]] = []
    agents: list[str] = []
    directives: list[str] = []
    seen_directive = False
    for raw_line in robots_txt.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.lower().startswith("user-agent:"):
            agent = line.split(":", 1)[1].strip().lower()
            if agents and seen_directive:
                groups.append((agents, directives))
                agents = []
                directives = []
                seen_directive = False
            agents.append(agent)
            continue
        if not agents:
            continue
        directives.append(line)
        seen_directive = True
    if agents:
        groups.append((agents, directives))

    exact_rate = 0
    wildcard_rate = 0
    for agents, directives in groups:
        applies_exact = user_agent in agents
        applies_wildcard = "*" in agents
        if not applies_exact and not applies_wildcard:
            continue
        for directive in directives:
            if not directive.lower().startswith("request-rate:"):
                continue
            value = directive.split(":", 1)[1].strip()
            rate_s = _parse_request_rate_value(value)
            if applies_exact and rate_s:
                exact_rate = rate_s
            elif applies_wildcard and rate_s and wildcard_rate == 0:
                wildcard_rate = rate_s
    return exact_rate or wildcard_rate


def _path_pattern(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    expression = re.escape(pattern).replace(r"\*", ".*")
    suffix = "$" if anchored else ""
    return re.compile(f"^{expression}{suffix}")


def can_fetch_path(robots_txt: str, user_agent: str, url: str) -> bool:
    product_token = user_agent.split("/", 1)[0].lower()
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    seen_rule = False
    for raw_line in robots_txt.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        name, value = (part.strip() for part in line.split(":", 1))
        name = name.lower()
        if name == "user-agent":
            if agents and seen_rule:
                groups.append((agents, rules))
                agents = []
                rules = []
                seen_rule = False
            agents.append(value.lower())
            continue
        if not agents or name not in {"allow", "disallow"}:
            continue
        if value or name == "allow":
            rules.append((name == "allow", value))
        seen_rule = True
    if agents:
        groups.append((agents, rules))

    matched_groups: list[tuple[int, list[tuple[bool, str]]]] = []
    for group_agents, group_rules in groups:
        specificities = [
            len(agent)
            for agent in group_agents
            if agent != "*" and agent in product_token
        ]
        if specificities:
            matched_groups.append((max(specificities), group_rules))
        elif "*" in group_agents:
            matched_groups.append((0, group_rules))
    if not matched_groups:
        return True

    best_agent_match = max(specificity for specificity, _ in matched_groups)
    selected_rules = [
        rule
        for specificity, group_rules in matched_groups
        if specificity == best_agent_match
        for rule in group_rules
    ]
    parsed = urlsplit(url)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    matches: list[tuple[int, bool]] = []
    for allowed, pattern in selected_rules:
        if not pattern or not _path_pattern(pattern).match(target):
            continue
        specificity = len(pattern.replace("*", "").removesuffix("$"))
        matches.append((specificity, allowed))
    if not matches:
        return True
    longest = max(specificity for specificity, _ in matches)
    return any(allowed for specificity, allowed in matches if specificity == longest)


def parse_robots(robots_txt: str, domain: str, user_agent: str) -> RobotsRules:
    parser = RobotFileParser()
    parser.parse(robots_txt.splitlines())
    delay = parser.crawl_delay(user_agent) or Settings.crawl_delay_default_s
    allowed = parser.can_fetch(user_agent, f"https://{domain}/")
    request_rate_s = _extract_request_rate(robots_txt, user_agent)
    return RobotsRules(
        domain=domain,
        crawl_delay_s=int(delay),
        request_rate_s=int(request_rate_s),
        allowed=allowed,
        fetched_at=int(time.time()),
        text=robots_txt,
    )


async def get_or_fetch_robots(
    redis_client: redis.Redis,
    session: aiohttp.ClientSession,
    domain: str,
    scheme: str = "https",
) -> RobotsRules:
    cache_key = robots_cache_key(domain, scheme)
    cached = await redis_client.hgetall(cache_key)
    if cached:
        return RobotsRules(
            domain=domain,
            crawl_delay_s=int(cached.get(b"crawl_delay_s", b"0")),
            request_rate_s=int(cached.get(b"request_rate_s", b"0")),
            allowed=cached.get(b"allowed", b"1") == b"1",
            fetched_at=int(cached.get(b"fetched_at", b"0")),
            text=cached.get(b"text", b"").decode(),
        )
    robots_txt = await fetch_robots_txt(session, domain, scheme)
    rules = parse_robots(robots_txt, domain, Settings.user_agent)
    await redis_client.hset(
        cache_key,
        mapping={
            "crawl_delay_s": rules.crawl_delay_s,
            "request_rate_s": rules.request_rate_s,
            "allowed": 1 if rules.allowed else 0,
            "fetched_at": rules.fetched_at,
            "text": rules.text,
        },
    )
    return rules


async def get_next_allowed(redis_client: redis.Redis, domain: str) -> int:
    value = await redis_client.get(robots_next_allowed_key(domain))
    if value is None:
        return 0
    return int(value)


async def update_next_allowed(
    redis_client: redis.Redis, domain: str, delay_s: int
) -> None:
    next_allowed = int(time.time()) + delay_s
    await redis_client.set(robots_next_allowed_key(domain), next_allowed)


async def reserve_next_allowed(
    redis_client: redis.Redis, domain: str, delay_s: int
) -> tuple[bool, int]:
    now = int(time.time())
    key = robots_next_allowed_key(domain)
    script = """
    local now = tonumber(ARGV[1])
    local delay = tonumber(ARGV[2])
    local current = tonumber(redis.call("GET", KEYS[1]) or "0")
    if current <= now then
        local next_allowed = now + delay
        redis.call("SET", KEYS[1], next_allowed)
        return {1, next_allowed}
    end
    return {0, current}
    """
    allowed, next_allowed = await redis_client.eval(script, 1, key, now, delay_s)
    return bool(int(allowed)), int(next_allowed)
