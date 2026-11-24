# Container qualification

**Status: BLOCKED on daemon access.** Docker Engine 29.1.3 and Docker Compose 2.40.3 are installed. The operator reports a successful privileged hello-world run. The qualification session independently confirmed that the socket was root:docker with mode 0660. The rkhurana account had no docker membership. Both docker ps and the isolated Compose build failed with permission denied on /var/run/docker.sock. sudo -n -l required interactive authentication. The Compose build also warned that its configured Bake path lacked Buildx. Neither host permissions nor daemon configuration were modified.

The checkout ran in WSL2 on an AMD Ryzen AI 7 350 with 16 logical CPUs, 7.3 GiB memory and 2.0 GiB swap. The qualification cgroup reported no CPU quota and no cgroup memory cap. The workspace filesystem reported 952 GiB available at inspection. The separate /tmp filesystem had 385 MiB free at initial inspection, so the large local benchmark used a clone of the existing synthetic database.

## Prepared topology

The base Compose file retains its SQLite profile. The production overlay adds nginx TLS ingress, removes the direct app host port, and separates the ingress and PostgreSQL networks. PostgreSQL uses an internal network without a published port. The app and PostgreSQL retain named volumes. nginx mounts the TLS certificate directory read-only. The app image declares UID 10001, drops capabilities through Compose, and has a database-backed readiness health check. PostgreSQL health gates app startup, and app health gates nginx startup. Compose config --quiet passed for the combined configuration using synthetic interpolation values.

Image build, runtime UID, internal connectivity, migrations inside the container, restart policies in action, worker recovery, and data and attachment persistence across container replacement are blocked. Successful non-container nginx and PostgreSQL checks do not close these gates.

## Resume after authorization

Docker group membership grants effectively root-level host access. After explicitly approving that change, an operator can run the following in WSL and launch Codex from the new group shell.

    sudo usermod -aG docker "$USER"
    newgrp docker
    docker ps

Alternatively, start a fresh WSL login session after changing membership. Confirm Docker works before starting a qualification run. Use dedicated credentials, a TLS directory with fullchain.pem and privkey.pem, and an isolated project name. Do not use existing production volumes.

    docker compose -p logentra-qualification -f compose.yaml -f compose.production.yaml --profile postgres config --quiet
    docker compose -p logentra-qualification -f compose.yaml -f compose.production.yaml --profile postgres up --build --wait

The overlay requires LOGENTRA_DATABASE_URL, LOGENTRA_POSTGRES_PASSWORD and LOGENTRA_TLS_DIRECTORY. The TLS port defaults to loopback port 8443. Verify TLS from the client, non-root execution inside the app, and evidence persistence across app and database restart before reusing the acceptance matrix.

The host test used one loopback nginx instance. The application's peer-based login limit sees the proxy connection, while nginx enforces its own client-address limit. Test login behavior under the real ingress topology before production acceptance.
