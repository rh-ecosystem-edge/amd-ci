import os
from typing import Optional

class Settings:
    ignored_versions: str
    version_file_path: str
    tests_to_trigger_file_path: str
    request_timeout_sec: int
    gpu_versions_to_test_count: Optional[int]

    def __init__(self):
        self.ignored_versions = os.getenv("OCP_IGNORED_VERSIONS_REGEX", "4\\.(7|8|9|10|11|12|13|14|15)").rstrip()
        self.version_file_path = os.getenv("VERSION_FILE_PATH")
        self.tests_to_trigger_file_path = os.getenv("TEST_TO_TRIGGER_FILE_PATH")
        self.request_timeout_sec = int(os.getenv("REQUEST_TIMEOUT_SECONDS", 30))
        
        # GPU_VERSIONS_TO_TEST_COUNT: Parameter to limit GPU versions for new OCP tests
        # - TEMPORARY: Default of 2 is set in the workflow YAML (test against latest 2 GPU versions)
        # - To revert: remove the || '2' fallback in the YAML file
        # - If set to a positive integer X: Test against only the latest X GPU operator versions
        gpu_count_env = os.getenv("GPU_VERSIONS_TO_TEST_COUNT", "").strip()
        if gpu_count_env:
            self.gpu_versions_to_test_count = int(gpu_count_env)
            if self.gpu_versions_to_test_count <= 0:
                raise ValueError("GPU_VERSIONS_TO_TEST_COUNT must be a positive integer when set")
        else:
            # Default: None means test all versions
            self.gpu_versions_to_test_count = None

        if not self.version_file_path:
            raise ValueError("VERSION_FILE_PATH must be specified")
        if not self.tests_to_trigger_file_path:
            raise ValueError("TEST_TO_TRIGGER_FILE_PATH must be specified")

