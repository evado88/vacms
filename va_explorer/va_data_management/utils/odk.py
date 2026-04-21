import logging
from io import BytesIO
from tempfile import NamedTemporaryFile

import environ
import pandas as pd
import requests
import toml
from pyodk import Client

env = environ.Env()

ODK_HOST = env("ODK_HOST", default="http://127.0.0.1:5002")
# Don't verify localhost (self-signed cert)
SSL_VERIFY = env.bool("ODK_SSL_VERIFY", not ODK_HOST.startswith("https://localhost"))

logger = logging.getLogger(__name__)


def flatten_dict(item):
    result = {}
    for key, value in item.items():
        if isinstance(value, dict):
            result.update(flatten_dict(value))
        else:
            result[key] = value
    return result


def get_odk_login_token(email, password):
    url = f"{ODK_HOST}/v1/sessions"
    response = requests.post(
        url, json={"email": email, "password": password}, verify=SSL_VERIFY
    )
    response.raise_for_status()
    data = response.json()

    token = data["token"]
    return {"Authorization": f"Bearer {token}"}


def get_odk_project_id(token, project_name):
    url = f"{ODK_HOST}/v1/projects/"
    response = requests.get(url, headers=token, verify=SSL_VERIFY)
    response.raise_for_status()
    projects = response.json()

    if not projects:
        raise ValueError("No projects were returned from ODK.")

    for proj in projects:
        if proj["name"] == project_name:
            return proj["id"]

    raise ValueError(f"No projects with name '{project_name}' were returned from ODK.")


def get_odk_form(token, project_id, form_name=None, form_id=None):
    if not form_name and not form_id:
        raise AttributeError("Must specify either form_name or form_id argument.")

    url = f"{ODK_HOST}/v1/projects/{project_id}/forms"
    response = requests.get(url, headers=token, verify=SSL_VERIFY)
    response.raise_for_status()
    forms = response.json()

    if not forms:
        raise ValueError(
            f"No forms for project with ID '{project_id}' were returned from ODK."
        )

    for form in forms:
        if form_id and form_id == form["xmlFormId"]:
            return form
        if form_name and form_name == form["name"]:
            return form

    raise ValueError(
        f"No forms found with name '{form_name}' or ID '{form_id}' were found in ODK."
    )


def download_responses(
    email,
    password,
    project_name=None,
    project_id=None,
    form_name=None,
    form_id=None,
    fmt="csv",
):
    if not project_name and not project_id:
        raise AttributeError("Must specify either project_name or project_id argument.")

    if not form_name and not form_id:
        raise AttributeError("Must specify either form_name or form_id argument.")

    if fmt not in ["csv", "json"]:
        raise AttributeError("The fmt argument must either be json or csv.")

    token = get_odk_login_token(email, password)

    if not project_id:
        project_id = get_odk_project_id(token, project_name)

    form = get_odk_form(token, project_id, form_name, form_id)

    if fmt == "json":
        url = f'{ODK_HOST}/v1/projects/{project_id}/forms/{form["xmlFormId"]}.svc/Submissions'
        response = requests.get(url, headers=token, verify=SSL_VERIFY)
        response.raise_for_status()
        data = response.json()
        if "value" in data:
            forms = [flatten_dict(item) for item in data["value"]]
            return pd.DataFrame.from_records(forms)
        return []

    if fmt == "csv":
        url = f'{ODK_HOST}/v1/projects/{project_id}/forms/{form["xmlFormId"]}/submissions.csv'
        response = requests.get(url, headers=token, verify=SSL_VERIFY)
        response.raise_for_status()
        forms = pd.read_csv(BytesIO(response.content))
        forms.columns = [c.rsplit("-", 1)[-1] for c in forms.columns]
        return forms


def _make_client():
    """Create a pyODK Client based on environment configuration."""
    return make_pyodk_client()


def make_pyodk_client(project_id=None):
    """Create a pyODK Client with optional project override."""
    cfg = {
        "central": {
            "base_url": ODK_HOST,
            "username": env("ODK_EMAIL"),
            "password": env("ODK_PASSWORD"),
            "default_project_id": (
                project_id if project_id is not None else env.int("ODK_PROJECT_ID")
            ),
        }
    }
    with NamedTemporaryFile(delete=False, suffix=".toml") as tmp:
        toml.dump(cfg, tmp)
        path = tmp.name
    client = Client(config_path=path)
    client.session.verify = SSL_VERIFY
    logger.debug(
        "Created pyODK client for project %s with host %s",
        cfg["central"]["default_project_id"],
        ODK_HOST,
    )
    return client


def pyodk_download_definition(form_id):
    """Return XLSForm definition bytes for the given form."""
    with _make_client().open() as client:
        resp = client.get(f"projects/{client.project_id}/forms/{form_id}.xlsx")
        resp.raise_for_status()
        return resp.content


def pyodk_download_table(form_id):
    """Download submission table for a form as a DataFrame."""
    with _make_client().open() as client:
        data = client.submissions.get_table(form_id=form_id)
        records = data.get("value", [])
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame.from_records(records)
        df.columns = [c.rsplit("-", 1)[-1] for c in df.columns]
        return df
