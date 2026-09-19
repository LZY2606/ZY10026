def pytest_report_teststatus(report, config):
    if report.when != "call":
        return None
    if ("test_unwrapped_exception_regression_matrix" in report.nodeid or
            "test_mutation_guard" in report.nodeid):
        config.get_terminal_writer().line(f"REGRESSION {report.nodeid}")
    return None
