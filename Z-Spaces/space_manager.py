import io
import os
import tarfile

import docker
import docker.errors

IMAGE = "spaces-runner:latest"
CONTAINER_PORT = 7860

_clients: dict[str, docker.DockerClient] = {}


def _client(node_target: str) -> docker.DockerClient:
    client = _clients.get(node_target)
    if client is None:
        client = docker.DockerClient(base_url=f"ssh://{node_target}", use_ssh_client=True)
        _clients[node_target] = client
    return client


def _container_name(space_name: str) -> str:
    return "space-" + space_name.replace("/", "-")


def _tar_dir(path: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for entry in os.listdir(path):
            tar.add(os.path.join(path, entry), arcname=entry)
    return buf.getvalue()


def deploy(node_target: str, space_name: str, local_path: str, port: int) -> None:
    """Copies local_path into a fresh container on node_target and starts it on port."""
    client = _client(node_target)
    name = _container_name(space_name)

    try:
        client.containers.get(name).remove(force=True)
    except docker.errors.NotFound:
        pass

    container = client.containers.create(
        IMAGE,
        name=name,
        detach=True,
        ports={f"{CONTAINER_PORT}/tcp": port},
        # Tells gradio it's mounted behind our proxy so it emits correctly-prefixed asset URLs.
        environment={"GRADIO_ROOT_PATH": f"/space_proxy/{space_name}"},
    )
    container.put_archive("/usr/src/app", _tar_dir(local_path))
    container.start()


def stop(node_target: str, space_name: str) -> None:
    client = _client(node_target)
    try:
        client.containers.get(_container_name(space_name)).remove(force=True)
    except docker.errors.NotFound:
        pass


def status(node_target: str, space_name: str) -> str:
    client = _client(node_target)
    try:
        container = client.containers.get(_container_name(space_name))
        container.reload()
        return container.status
    except docker.errors.NotFound:
        return "stopped"
    except Exception:
        return "unreachable"


def logs(node_target: str, space_name: str, tail: int = 200) -> str:
    client = _client(node_target)
    try:
        container = client.containers.get(_container_name(space_name))
        return container.logs(tail=tail, stdout=True, stderr=True).decode(errors="replace")
    except docker.errors.NotFound:
        return ""
