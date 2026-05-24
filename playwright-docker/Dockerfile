ARG BASE_IMAGE=localhost/claude-default:latest
FROM ${BASE_IMAGE}

ARG PLAYWRIGHT_VERSION=1.60.0

# Shared, world-readable browser location (mirrors the official Playwright image),
# so the browser is found regardless of which user runs tests.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install the test runner globally, as node (same pattern as claude-code in the base).
RUN npm install -g @playwright/test@${PLAYWRIGHT_VERSION}

# Install Chromium + its apt OS dependencies. --with-deps needs root (apt-get);
# the browser is downloaded into PLAYWRIGHT_BROWSERS_PATH. Make it readable by node.
USER root
RUN playwright install --with-deps chromium \
  && chmod -R o+rX /ms-playwright
USER node
