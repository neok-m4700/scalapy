"""
=============================================
Lowlevel Interface (:mod:`~scalapy.lowlevel`)
=============================================

This module imports almost all the routines from ``Scalapack`` in a very
rudimentary form. They present a very basic ``f2py`` derived wrapper, with the
argument specification derived from the documentation embedded in the NETLIB_
source code. This approach seems to be very successful but your mileage may
vary. It is essential to look at the Scalapack documentation and know what you
are calling.

.. warning::

    Scalapack performs no bounds checking. The arrays passed into each
    Scalapack routine must be the size expected from the size and distribution
    that are specified. Failure to do this will result in at best memory
    corruption and worst segfaults.

.. _NETLIB: http://www.netlib.org/scalapack/

Argument Expansion
==================

Despite the routines being low-level there is a trick to make the process
easier - automatic expansion of arguments - which exploits the fact that many
routines in Scalapack have a common structure:

- Distributed matrices are passed in followed by their start indices and their
  descriptor.
- Work arrays for each type are passed in followed by their length. The
  required length is often a little hard to determine, however, most routines
  allow for a query where they will return the optimal length.

Taking the function :func:`pzheevd` as an example, a minimal call in the
standard manner would be something like::

    dA = core.DistributedMatrix([N, N], dtype=np.complex128)
    # ... initialise matrix

    evals = np.zeros(N, dtype=np.float64)
    evecs = core.DistributedMatrix([N, N], dtype=np.complex128)

    iwork = np.zeros(1, dtype=np.int32)
    dwork = np.zeros(1, dtype=np.float64)
    zwork = np.zeros(1, dtype=np.complex128)

    # Perform work query
    info = lowlevel.pzheevd(b'V', b'L', N,
                            dA.local_array, 1, 1, dA.desc,
                            evals,
                            evecs.local_array, 1, 1, evecs.desc,
                            zwork, -1, dwork, -1, iwork, -1)

    # Setup work arrays
    liwork = int(iwork[0])
    iwork = np.zeros(liwork, dtype=np.int32)
    ldwork = int(dwork[0])
    dwork = np.zeros(ldwork, dtype=np.float64)
    lzwork = int(zwork[0])
    zwork = np.zeros(lzwork, dtype=np.complex128)

    # Perform computation
    info = lowlevel.pzheevd(b'V', b'L', N,
                            dA.local_array, 1, 1, dA.desc,
                            evals,
                            evecs.local_array, 1, 1, evecs.desc,
                            zwork, lzwork, dwork, ldwork, iwork, liwork)

In here we have had to make manual queries for the size of the work arrays,
and allocate them, which requires two calls to the ``Scalapack`` routine, and
a large amount of initialisation. In each, of those calls we need to insert
all the parameters for the distributed matrices. The bulk of the code is just
setting up everything for the final call.

Argument expansion means we can remove most of the complexity. This is the
same call using it::

    dA = core.DistributedMatrix([N, N], dtype=np.complex128)
    # ... initialise matrix

    evals = np.zeros(N, dtype=np.float64)
    evecs = core.DistributedMatrix([N, N], dtype=np.complex128)

    # Call routine
    info = lowlevel.pzheevd('V', 'L', N, dA, evals, evecs,
                            lowlevel.WorkArray('Z', 'D', 'I'))

Using this, any :class:`~scalapy.core.DistributedMatrix` passed as an
argument, automatically gets expanded from ``(..., dA, ...)`` to the standard
Scalapack argument pattern ``(..., dA.local_array, 1, 1, dA.desc, ...)``,.
This is a useful shortcut when constructing argument lists, however there is
no requirement to use this, the full form can still be passed in manually.

However, much more useful is expansion of the :class:`WorkArray` in place of
the full work array arguments. This causes it to automatically call the
underlying Scalapack routine twice, the first time performing a work query for
which it inserts the correct arguments, and then on the subsequent call it
uses the query result to initialising temporary work arrays which are passed
into the routine for computation. This behaviour can save a substantial amount
of time manually querying and initialising temporary arrays.

A final transformation is that string arguments are automatically sanitised and
converted to ASCII string held in a byte array. On Python 2 this is not needed,
but for Python 3 we need to ensure this is done properly.


Classes
=======

.. autosummary::
    :toctree: generated/

    WorkArray


PBLAS Routines
==============

.. autosummary::
    :toctree: generated/

<_insert_pblas>


Scalapack Routines
==================

.. autosummary::
    :toctree: generated/

<_insert_scalapack>

"""
from __future__ import print_function, division

import numpy as np

from .. import core, util
from . import pblas as _pblas
from . import scalapack as _scl
from . import redist as _redist

expand_args = True


def _expand_work(args, query=True):
    # Go through an argument list and expand and WorkArrays found.

    exp_args = []
    for arg in args:
        if isinstance(arg, WorkArray):
            arg = arg.to_query() if query else arg.to_compute()
        exp_args.append(arg)
    return exp_args


def _expand_dm(args):
    # Iterate through and expand any DistributedMatrices found.

    exp_args = []
    for arg in args:
        if isinstance(arg, core.DistributedMatrix):
            arg = [ arg._local_array, 1, 1, arg.desc ]
        exp_args.append(arg)
    return exp_args


def _encode_strings(args):
    # Ensure any string arguments get turned into proper 1-byte ascii so
    # ScaLAPACK doesn't get confused

    def _fix_string(arg):
        if isinstance(arg, str):
            return arg.encode('ascii')
        return arg

    return [_fix_string(arg) for arg in args]


def _call_routine(routine, *args):
    # Call the routine, expanding any arguments as required.

    # Check to see if there any WorkArrays
    need_workquery = any([isinstance(arg, WorkArray) for arg in args])

    # Expand the DM arguments
    exp_args = _expand_dm(args)

    # Convert any strings to bytes (potentially an issue for Python 3)
    exp_args = _encode_strings(exp_args)

    # Perform a WorkArray query if needed
    if need_workquery:
        wq_args = _expand_work(exp_args, query=True)
        rv = routine(*util.flatten(wq_args))

    # Call the routine for real, with any Work arrays allocated.
    wc_args = _expand_work(exp_args, query=False)
    rv = routine(*util.flatten(wc_args))

    return rv


def _wrap_routine(rname, robj):
    # Generate a wrapper around the lowlevel routine which can expand the
    # arguments if required.

    # Create wrapper
    def wrapper(*args):
        if expand_args:
            return _call_routine(robj, *args)
        else:
            robj(*util.flatten(args))

    # Set the function name
    wrapper.__name__ = rname

    # Give doc string if it has one.
    if hasattr(robj, '__doc__'):
        wrapper.__doc__ = robj.__doc__

    return wrapper


class WorkArray(object):
    """Helper to deal with Scalapack work array entries.

    This class can be used to help with both workspace queries and allocating
    temporary arrays for the workspace. It should be passed to a Scalapack
    routine, in the form ``WorkArray('Z', 'D')``, where ``Z`` and ``D`` are
    character codes giving the work array types. Possible values are ``I``
    (integer), ``S`` (single precision float), ``D`` (double precision float),
    ``C`` (single precision complex) and ``Z`` (double precision float).

    Parameters
    ----------
    typecodes : selection of { 'I', 'S', 'D', 'C', 'Z' }
        Character codes listing the types of the work arrays required in
        order of their sequence in the call.
    """

    types = None
    np_types = None
    query_arrays = None

    def __init__(self, *args):
        """Create a set of Scalapack work arrays.

        """

        _typemap = {'S': np.float32,
                    'C': np.complex64,
                    'D': np.float64,
                    'Z': np.complex128,
                    'I': np.int32}

        types = args

        self.types = types
        self.np_types = [ _typemap[type_] for type_ in self.types ]

    def to_query(self):
        """Return a list of arguments for each work array to do a workspace
        query.

        This will create length-1 arrays to hold the result of the query.
        """

        self.query_arrays = [ np.zeros(1, dtype=type_) for type_ in self.np_types ]

        query_list = [ [arr, -1] for arr in self.query_arrays ]

        return query_list

    def to_compute(self):
        """Having performed a workspace query, return the arguments containing
        the temporary work arrays and their lengths.

        Must have actually performed a query first in order to have set the
        workspace lengths.
        """

        if self.query_arrays is None:
            raise Exception("Work query not yet performed.")

        wlens = [ int(np.real(arr[0])) for arr in self.query_arrays ]

        work_list = [ [ np.zeros(wlen, dtype=type_), wlen] for wlen, type_ in zip(wlens, self.np_types) ]

        return work_list


# Add wrapped routines to this modules dictionary.
# Also try and insert the PBLAS and ScaLAPACK routines into the docstring.
_mod_dict = globals()

_doc_redist = ''
_doc_pblas = ''
_doc_scl = ''


# From REDIST
for rname, robj in _redist.__dict__.items():
    if type(robj).__name__ == 'fortran':
        _mod_dict[rname] = _wrap_routine(rname, robj)
        _doc_redist += '    ' + rname + '\n'

_mod_dict['__doc__'] = _mod_dict['__doc__'].replace('<insert_redist>', _doc_redist)


# From PBLAS
for rname, robj in _pblas.__dict__.items():
    if type(robj).__name__ == 'fortran':
        _mod_dict[rname] = _wrap_routine(rname, robj)
        _doc_pblas += '    ' + rname + '\n'

_mod_dict['__doc__'] = _mod_dict['__doc__'].replace('<insert_pblas>', _doc_pblas)

# From Scalapack
for rname, robj in _scl.__dict__.items():
    if type(robj).__name__ == 'fortran':
        _mod_dict[rname] = _wrap_routine(rname, robj)
        _doc_scl += '    ' + rname + '\n'

_mod_dict['__doc__'] = _mod_dict['__doc__'].replace('<insert_scalapack>', _doc_scl)
