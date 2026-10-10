import type { E2EConfig } from 'e2e';
import { web } from '@e2e-dev/web';

// All tests are deterministic; no model or paid test service is required.
export default {
  targets: [
    {
      engine: web(),
      app: {
        url: 'http://127.0.0.1:8765',
        command: {
          executable: 'sh',
          args: ['-c', 'PYTHONPATH=src python3 -m tegara.dashboard --port 8765'],
          reuseExisting: true,
        },
      },
    },
  ],
} satisfies E2EConfig;
