# Site-isolated LibreChat companion

`install_site_companion.py` is the supported production installation entrypoint when a Frappe site
needs both `ione_agent` and LibreChat. Run it on the Docker/Frappe host as root. It keeps host Docker
control out of the Frappe web containers.

Example:

```bash
sudo python3 deploy/install_site_companion.py \
  --site child.example.com \
  --frontend-domain agent-child.example.com \
  --librechat-port 13080 \
  --bridge-port 18100
```

The command is idempotent and performs these operations:

1. Installs `ione_agent` on the selected site if needed.
2. Clones or fast-forwards the configured LibreChat fork and builds an image tagged with its commit.
3. Creates a dedicated LibreChat, MongoDB and Meilisearch Compose project and data directory.
4. Creates a dedicated Codex bridge process, Frappe integration user, API token, SSO secrets and data
   directories for the site.
5. Configures the Frappe SSO redirect and starts both services with systemd.
6. Verifies the bridge and LibreChat health endpoints.

Every site must use different `--librechat-port`, `--bridge-port` and `--frontend-domain` values.
The generated services and Docker project names include the site slug, so their containers, networks,
MongoDB, Meilisearch and uploaded files cannot collide with another site's LibreChat instance.

The script does not edit DNS or tunnel routing. Point the frontend domain at the selected LibreChat host
port after provisioning.
