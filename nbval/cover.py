"""
Code to enable coverage of any external code called by the
notebook.
"""

import os
import coverage


# Coverage setup/teardown code to run in kernel
# Inspired by pytest-cov code.
_python_setup = """\
import coverage

__cov = coverage.Coverage(
    data_file=%r,
    source=%r,
    config_file=%r,
    auto_data=True,
    data_suffix=%r,
    )
__cov.load()
__cov.start()
__cov._warn_no_data = False
__cov._warn_unimported_source = False
"""
_python_teardown = """\
__cov.stop()
__cov.save()
"""


def setup_coverage(config, kernel, floc, output_loc=None):
    """Start coverage reporting in kernel.

    Currently supported kernel languages are:
     - Python
    """

    language = kernel.language
    if language.startswith('python'):
        # Get the pytest-cov coverage object
        cov = get_cov(config)
        if cov:
            # If present, copy the data file location used by pytest-cov
            data_file = os.path.abspath(cov.config.data_file)
        else:
            # Fall back on output_loc and current dir if not
            data_file = os.path.abspath(os.path.join(output_loc or os.getcwd(), '.coverage'))

        # Get options from pytest-cov's command line arguments:
        source = config.option.cov_source
        config_file = config.option.cov_config

        # Copy the suffix of plugin if available
        suffix = _make_suffix(cov)
        if suffix is True:
            # Cannot merge data with autogen suffix, so turn off warning
            # for missing data in pytest-cov collector
            cov._warn_no_data = False

        # Build setup command and execute in kernel:
        cmd = _python_setup % (data_file, source, config_file, suffix)
        msg_id = kernel.kc.execute(cmd, stop_on_error=False)
        kernel.await_idle(msg_id, 60)  # A minute should be plenty to enable coverage
    else:
        config.warn(
            'C1',
            'Coverage currently not supported for language "%s".' % language,
            floc)
        return


def teardown_coverage(config, kernel, output_loc=None):
    """Finish coverage reporting in kernel.

    The coverage should previously have been started with
    setup_coverage.
    """
    language = kernel.language
    if language.startswith('python'):
        # Teardown code does not require any input, simply execute:
        msg_id = kernel.kc.execute(_python_teardown)
        kernel.await_idle(msg_id, 60)  # A minute should be plenty to write out coverage

        # Ensure we merge our data into parent data of pytest-cov, if possible
        cov = get_cov(config)
        _merge_nbval_coverage_data(cov)

    else:
        # Warnings should be given on setup, or there might be no teardown
        # for a specific language, so do nothing here
        pass


def get_cov(config):
    """Returns the coverage object of pytest-cov."""

    # Check with hasplugin to avoid getplugin exception in older pytest.
    if config.pluginmanager.hasplugin('_cov'):
        plugin = config.pluginmanager.getplugin('_cov')
        if plugin.cov_controller:
            return plugin.cov_controller.cov
    return None


def _make_suffix(cov):
    """Create a suffix for nbval data file depending on pytest-cov config."""
    # Check if coverage object has data_suffix:
    if cov and cov.data_suffix is not None:
        # If True, the suffix will be autogenerated by coverage.py.
        # The suffixed data files will be automatically combined later.
        if cov.data_suffix is True:
            return True
        # Has a suffix, but we add our own extension
        return cov.data_suffix + '.nbval'
    return 'nbval'


def _merge_nbval_coverage_data(cov):
    """Merge nbval coverage data into pytest-cov data."""
    if not cov:
        return

    suffix = _make_suffix(cov)
    if suffix is True:
        # Note: If suffix is true, we are running in parallel, so several
        # files will be generated. This will cause some warnings about "no coverage"
        # but is otherwise OK. Do nothing.
        return

    # Get the filename of the nbval coverage:
    filename = cov.data_files.filename + '.' + suffix

    # Read coverage generated by nbval in this run:
    nbval_data = coverage.CoverageData(debug=cov.debug)
    try:
        nbval_data.read_file(os.path.abspath(filename))
    except coverage.CoverageException:
        return

    # Set up aliases (following internal coverage.py code here)
    aliases = None
    if cov.config.paths:
        aliases = coverage.files.PathAliases()
        for paths in cov.config.paths.values():
            result = paths[0]
            for pattern in paths[1:]:
                aliases.add(pattern, result)

    # Merge nbval data into pytest-cov data:
    cov.data.update(nbval_data, aliases=aliases)
    # Delete our nbval coverage data
    coverage.misc.file_be_gone(filename)


"""
Note about coverage data/datafiles:

When pytest is running, we get the pytest-cov coverage object.
This object tracks its own coverage data, which is stored in its
data file. For several reasons detailed below, we cannot use the
same file in the kernel, so we have to ensure our own, and then
ensure that they are all merged correctly at the end. The important
factor here is the data_suffix attribute which might be set.

Cases:
1. data_suffix is set to None:
   No suffix is used by pytest-cov. We need to create a new file,
   so we add a suffix for kernel, and then merge this file into
   the pytest-cov data at teardown.
2. data_suffix is set to a string:
   We need to create a new file, so we append a string to the
   suffix passed to the kernel. We merge this file into the
   pytest-cov data at teardown.
3. data_suffix is set to True:
   The suffix will be autogenerated by coverage.py, along the lines
   of 'hostname.pid.random'. This is typically used for parallel
   tests. We pass True as suffix to kernel, ensuring a unique
   auto-suffix later. We cannot merge this data into the pytest-cov
   one, as we do not know the suffix, but we can just leave the data
   for automatic collection. However, this might lead to a warning
   about no coverage data being collected by the pytest-cov
   collector.

Why do we need our own coverage data file?
Coverage data can get lost if we try to sync via load/save/load cycles
between the two. By having our own file, we can do an in-memory merge
of the data afterwards using the official API. Either way, the data
will always be merged to one coverage file in the end, so these files
are transient.
"""
