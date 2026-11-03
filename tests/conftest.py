import os
import tempfile

# Tes tidak boleh menyentuh (atau memigrasikan) folder data asli di %APPDATA%.
os.environ["DUCKLIVE_DATA"] = tempfile.mkdtemp(prefix="ducklive-test-")
