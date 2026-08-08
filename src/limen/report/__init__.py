from . import human, json as json_report, junit, sarif  # noqa: F401

FORMATS = {
    "human": human.render,
    "json": json_report.render,
    "junit": junit.render,
    "sarif": sarif.render,
}
