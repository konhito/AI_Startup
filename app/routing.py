"""Explicit provider selection. Only transient availability errors may fall back."""
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Route:
    provider: str
    model: str
    input_rate: int
    output_rate: int
    mock: bool = False


def openrouter_enabled():
    return bool(os.getenv('OPENROUTER_API_KEY', '').strip())


def arliai_enabled():
    return bool(os.getenv('ARLIAI_API_KEY', '').strip())


def arliai_route():
    return Route('arliai', os.getenv('ARLIAI_MODEL', 'Qwen3.5-27B-Derestricted'),
        int(os.getenv('ARLIAI_INPUT_PRICE_MICRO_USD', '0')),
        int(os.getenv('ARLIAI_OUTPUT_PRICE_MICRO_USD', '0')))


def self_hosted(mock):
    return Route('mock' if mock else 'self-hosted', os.getenv('MODEL_NAME', 'glm'),
        int(os.getenv('INPUT_PRICE_MICRO_USD', '200000')),
        int(os.getenv('OUTPUT_PRICE_MICRO_USD', '800000')), mock)


def select_route(model, mock):
    if model == 'qwen-derestricted':
        return arliai_route()
    if model == 'glm-5.3':
        return Route('openrouter', os.getenv('OPENROUTER_GLM53_MODEL', 'z-ai/glm-5.3'),
            int(os.getenv('OPENROUTER_GLM53_INPUT_PRICE_MICRO_USD', '1500000')),
            int(os.getenv('OPENROUTER_GLM53_OUTPUT_PRICE_MICRO_USD', '5000000')))
    if model == 'auto':
        if arliai_enabled():
            return arliai_route()
        if openrouter_enabled():
            return Route('openrouter', os.getenv('OPENROUTER_MODEL', 'deepseek/deepseek-v4.1-flash-1017'),
                int(os.getenv('OPENROUTER_INPUT_PRICE_MICRO_USD', '500000')),
                int(os.getenv('OPENROUTER_OUTPUT_PRICE_MICRO_USD', '1000000')))
    return self_hosted(mock)


def can_fallback(route, status, mock):
    # Never switch from a real provider to a fake response, or retry refusals,
    # authentication, payment, invalid-request or content-filter errors.
    return (route.provider == 'openrouter' and not mock
        and os.getenv('ROUTER_FALLBACK', 'true').lower() == 'true'
        and status in (429, 500, 502, 503, 504))


def endpoint(route):
    if route.provider == 'arliai':
        return os.getenv('ARLIAI_BASE_URL', 'https://api.arliai.com/v1').rstrip('/') + '/chat/completions', {
            'Authorization': 'Bearer ' + os.environ['ARLIAI_API_KEY'],
        }
    if route.provider == 'openrouter':
        return 'https://openrouter.ai/api/v1/chat/completions', {
            'Authorization': 'Bearer ' + os.environ['OPENROUTER_API_KEY'],
            'X-Title': 'Unbound',
        }
    return os.environ['UPSTREAM_URL'].rstrip('/') + '/v1/chat/completions', {
        'Authorization': 'Bearer ' + os.environ['UPSTREAM_API_KEY'],
    }
