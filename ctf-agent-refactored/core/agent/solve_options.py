"""Per-run solve limits; overriding these never changes config.yaml."""
MIN_TOKEN_BUDGET = 1000
MAX_TOKEN_BUDGET = 10_000_000


def validate_token_budget(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_TOKEN_BUDGET <= value <= MAX_TOKEN_BUDGET:
        raise ValueError(f'Token 预算须为 {MIN_TOKEN_BUDGET:,} 到 {MAX_TOKEN_BUDGET:,} 之间的整数')
    return value


def solve_options(settings):
    return {'token_budget': settings.solver_token_budget,
            'min_token_budget': MIN_TOKEN_BUDGET, 'max_token_budget': MAX_TOKEN_BUDGET}
