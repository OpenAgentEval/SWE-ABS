"""
Utilities for generating Docker Hub image URIs for SWE-bench Pro instances.

This module provides functions to convert instance IDs and repository names
into properly formatted Docker Hub image URIs that match the expected format
from the upload scripts.

Docker Hub Image Format:
    jefzda/sweap-images:{repo_base}.{repo_name}-{repo_base}__{repo_name}-{hash}-{version}

Example:
    jefzda/sweap-images:gravitational.teleport-gravitational__teleport-82185f232ae8974258397e121b3bc2ed0c3729ed-v626ec2a48416b10a88641359a169d99e935ff03

Note: bash runs by default in our images. When running these images, you should
not manually invoke bash.
"""


def create_dockerhub_tag(uid, repo_name=""):
    """
    Convert instance_id and repo name to Docker Hub compatible tag format.
    This must match the format used in the upload script.

    Args:
        uid (str): The instance_id (e.g., "instance_django__django-12345-v...")
        repo_name (str): The repository name (e.g., "NodeBB/NodeBB")

    Returns:
        str: Docker Hub compatible tag (e.g., "nodebb.nodebb-NodeBB__NodeBB-...")
    """
    if repo_name:
        # For "NodeBB/NodeBB" -> repo_base="nodebb", repo_name="nodebb"
        # Format: {repo_base}.{repo_name}-{OriginalCase}__{OriginalCase}-{hash}-{version}
        # Example: nodebb.nodebb-NodeBB__NodeBB-7b8bffd763e2155cf88f3ebc258fa68ebe18188d-vf2cf3cbd463b7ad942381f1c6d077626485a1e9e
        repo_base, repo_name_only = repo_name.lower().split("/")
        # Keep original case for the instance_id part (after removing "instance_" prefix)
        hsh = uid.replace("instance_", "")
        return f"{repo_base}.{repo_name_only}-{hsh}"
    else:
        image_name = "default"

    # Extract the tag part from the instance ID
    # For UIDs that start with a pattern like "django__django-", extract everything after position 9
    if "__" in uid and len(uid) > 9:
        tag_part = uid[9:]  # Skip the first 9 characters (e.g., "instance_")
    else:
        tag_part = uid

    return f"{image_name}-{tag_part}"


def create_dockerhub_image_uri_from_instance_id(instance_id, dockerhub_username="jefzda"):
    """
    Convert instance_id to Docker Hub image URI without requiring repo_name.
    Parses repo_base and repo_name from the instance_id itself.

    Args:
        instance_id (str): The instance directory name (e.g., "instance_django__django-12345-v...")
        dockerhub_username (str): Docker Hub username (default: jefzda)

    Returns:
        str: Full Docker Hub image URI

    Example:
        instance_id: instance_gravitational__teleport-82185f...-v626ec...
        returns: jefzda/sweap-images:gravitational.teleport-gravitational__teleport-82185f...-v626ec...
    """
    # Remove 'instance_' prefix if present
    if instance_id.startswith("instance_"):
        uid = instance_id[9:]  # e.g., "gravitational__teleport-82185f...-v626ec..."
    else:
        uid = instance_id

    # Parse repo_base and repo_name from the uid
    # Format: {repo_base}__{repo_name}-{hash}-{version}
    if "__" in uid:
        parts = uid.split("__", 1)
        repo_base = parts[0].lower()  # e.g., "gravitational"
        # The rest contains repo_name-hash-version
        rest = parts[1]
        # Extract repo_name (before the first '-' that's followed by a hash)
        # e.g., "teleport-82185f...-v626ec..." -> repo_name="teleport"
        dash_idx = rest.find("-")
        if dash_idx > 0:
            repo_name = rest[:dash_idx].lower()  # e.g., "teleport"
        else:
            repo_name = rest.lower()

        # Special handling for element-hq/element-web
        # Special case: keep full name for one specific instance
        if uid == "element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan":
            repo_name = "element-web"
        elif "element-hq" in repo_base and "element-web" in rest.lower():
            repo_name = "element"
            # Strip -vnan suffix for element-hq cases
            if uid.endswith("-vnan"):
                uid = uid[:-5]
        # Strip -vnan suffix for all other repos
        elif uid.endswith("-vnan"):
            uid = uid[:-5]

        # Build tag: {repo_base}.{repo_name}-{original_uid}
        tag = f"{repo_base}.{repo_name}-{uid}"
    else:
        # Fallback: use default format
        tag = f"default-{uid}"

    # Truncate if too long (Docker Hub tag limit is 128 chars)
    if len(tag) > 128:
        tag = tag[:128]

    return f"{dockerhub_username}/sweap-images:{tag}"


def get_dockerhub_image_uri(uid, dockerhub_username, repo_name=""):
    """
    Legacy function for backwards compatibility.
    Convert instance_id and repo_name to Docker Hub image URI.

    Args:
        uid (str): The instance_id (e.g., "instance_django__django-12345-v...")
        dockerhub_username (str): Docker Hub username
        repo_name (str): The repository name (e.g., "NodeBB/NodeBB")

    Returns:
        str: Full Docker Hub image URI
    """
    repo_base, repo_name_only = repo_name.lower().split("/")
    hsh = uid.replace("instance_", "")

    if uid == "instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan":
        repo_name_only = 'element-web'  # Keep full name for this one case
    elif 'element-hq' in repo_name.lower() and 'element-web' in repo_name.lower():
        repo_name_only = 'element'
        if hsh.endswith('-vnan'):
            hsh = hsh[:-5]
    # All other repos: strip -vnan suffix
    elif hsh.endswith('-vnan'):
        hsh = hsh[:-5]

    tag = f"{repo_base}.{repo_name_only}-{hsh}"
    if len(tag) > 128:
        tag = tag[:128]

    return f"{dockerhub_username}/sweap-images:{tag}"

