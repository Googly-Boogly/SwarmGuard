"""Export the server's actual OpenAPI contract without starting a server or making requests."""
import json
from pathlib import Path
from swarmguard.console.api import create_console_app

Path('frontend/openapi.json').write_text(json.dumps(create_console_app().openapi(), indent=2) + '\n')
