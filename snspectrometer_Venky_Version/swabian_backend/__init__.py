"""Adapter layer around the official Swabian Instruments TimeTagger Python API.

Every direct call into the ``TimeTagger`` module lives in this package. The rest of
the application only sees the wrappers defined here, so a library upgrade or a
mock replacement touches this package alone.

Verified against: Time Tagger User Manual release 2.22.6 and the installed
package 2.21.2. Where the two differ (``GatedCounter`` exists only from 2.22 on)
the adapter selects the available implementation at runtime and reports which
one was used.
"""

from .api import SwabianAPI, SwabianUnavailable, get_api, set_api_module  # noqa: F401
