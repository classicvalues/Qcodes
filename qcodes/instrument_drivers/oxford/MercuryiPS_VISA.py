from functools import partial
from typing import Dict, Union, Optional, Callable
import logging

from qcodes.instrument.channel import InstrumentChannel
from qcodes.instrument.visa import VisaInstrument
from qcodes.math.field_vector import FieldVector

log = logging.getLogger(__name__)


def _response_preparser(bare_resp: str) -> str:
    """
    Pre-parse response from the instrument
    """
    return bare_resp.replace(':', '')


def _signal_parser(our_scaling: float, response: str) -> float:
    """
    Parse a response string into a correct SI value.

    Args:
        our_scaling: Whatever scale we might need to apply to get from
            e.g. A/min to A/s.
        response: What comes back from instrument.ask
    """

    # there might be a scale before the unit. We only want to deal in SI
    # units, so we translate the scale
    scale_to_factor = {'n': 1e-9, 'u': 1e-6, 'm': 1e-3,
                       'k': 1e3, 'M': 1e6}

    numchars = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '.', '-']

    response = _response_preparser(response)
    digits = ''.join([d for d in response if d in numchars])
    scale_and_unit = response[len(digits):]
    if scale_and_unit == '':
        their_scaling: float = 1
    elif scale_and_unit[0] in scale_to_factor.keys():
        their_scaling = scale_to_factor[scale_and_unit[0]]
    else:
        their_scaling = 1

    return float(digits)*their_scaling*our_scaling


class MercurySlavePS(InstrumentChannel):
    """
    Class to hold a slave power supply for the MercuryiPS
    """

    def __init__(self, parent: VisaInstrument, name: str, UID: str) -> None:
        """
        Args:
            parent: The Instrument instance of the MercuryiPS
            name: The 'colloquial' name of the PS
            UID: The UID as used internally by the MercuryiPS, e.g.
                'GRPX'
        """
        if ':' in UID:
            raise ValueError('Invalid UID. Must be axis group name or device '
                             'name, e.g. "GRPX" or "PSU.M1"')

        super().__init__(parent, name)
        self.uid = UID

        self.add_parameter('voltage',
                           label='Output voltage',
                           get_cmd=partial(self._param_getter, 'SIG:VOLT'),
                           unit='V',
                           get_parser=partial(_signal_parser, 1))

        self.add_parameter('current',
                           label='Output current',
                           get_cmd=partial(self._param_getter, 'SIG:CURR'),
                           unit='A',
                           get_parser=partial(_signal_parser, 1))

        self.add_parameter('current_persistent',
                           label='Output persistent current',
                           get_cmd=partial(self._param_getter, 'SIG:PCUR'),
                           unit='A',
                           get_parser=partial(_signal_parser, 1))

        self.add_parameter('current_target',
                           label='Target current',
                           get_cmd=partial(self._param_getter, 'SIG:CSET'),
                           unit='A',
                           get_parser=partial(_signal_parser, 1))

        self.add_parameter('field_target',
                           label='Target field',
                           get_cmd=partial(self._param_getter, 'SIG:FSET'),
                           set_cmd=partial(self._param_setter, 'SIG:FSET'),
                           unit='T',
                           get_parser=partial(_signal_parser, 1))

        # NB: The current ramp rate slavishly follows the field ramp rate
        # (converted via the ATOB param)
        self.add_parameter('current_ramp_rate',
                           label='Ramp rate (current)',
                           unit='A/s',
                           get_cmd=partial(self._param_getter, 'SIG:RCST'),
                           get_parser=partial(_signal_parser, 1/60))

        self.add_parameter('field_ramp_rate',
                           label='Ramp rate (field)',
                           unit='T/s',
                           set_cmd=partial(self._param_setter, 'SIG:RFST'),
                           get_cmd=partial(self._param_getter, 'SIG:RFST'),
                           get_parser=partial(_signal_parser, 1/60),
                           set_parser=lambda x: x*60)

        self.add_parameter('field',
                           label='Field strength',
                           unit='T',
                           get_cmd=partial(self._param_getter, 'SIG:FLD'),
                           get_parser=partial(_signal_parser, 1))

        self.add_parameter('field_persistent',
                           label='Persistent field strength',
                           unit='T',
                           get_cmd=partial(self._param_getter, 'SIG:PFLD'),
                           get_parser=partial(_signal_parser, 1))

        self.add_parameter('ATOB',
                           label='Current to field ratio',
                           unit='A/T',
                           get_cmd=partial(self._param_getter, 'ATOB'),
                           get_parser=partial(_signal_parser, 1),
                           set_cmd=partial(self._param_setter, 'ATOB'))

        self.add_parameter('ramp_status',
                           label='Ramp status',
                           get_cmd=partial(self._param_getter, 'ACTN'),
                           set_cmd=self._ramp_status_setter,
                           get_parser=_response_preparser,
                           val_mapping={'HOLD': 'HOLD',
                                        'TO SET': 'RTOS',
                                        'CLAMP': 'CLMP',
                                        'TO ZERO': 'RTOZ'})

    def _ramp_status_setter(self, cmd: str) -> None:
        status_now = self.ramp_status()
        if status_now == 'CLAMP' and cmd == 'RTOS':
            raise ValueError(f'Error in ramping unit {self.uid}: '
                             'Can not ramp to target value; power supply is '
                             'clamped. Unclamp first by setting ramp status '
                             'to HOLD.')
        else:
            partial(self._param_setter, 'ACTN')(cmd)

    def _param_getter(self, get_cmd: str) -> str:
        """
        General getter function for parameters

        Args:
            get_cmd: raw string for the command, e.g. 'SIG:VOLT'

        Returns:
            The response. Cf. MercuryiPS.ask for how much is returned
        """

        dressed_cmd = '{}:{}:{}:{}:{}'.format('READ', 'DEV', self.uid, 'PSU',
                                              get_cmd)
        resp = self._parent.ask(dressed_cmd)

        return resp

    def _param_setter(self, set_cmd: str, value: Union[float, str]) -> None:
        """
        General setter function for parameters

        Args:
            set_cmd: raw string for the command, e.g. 'SIG:FSET'
        """
        dressed_cmd = '{}:{}:{}:{}:{}:{}'.format('SET', 'DEV', self.uid, 'PSU',
                                                 set_cmd, value)
        # the instrument always very verbosely responds
        # the return value of `ask`
        # holds the value reported back by the instrument
        self._parent.ask(dressed_cmd)

        # TODO: we could use the opportunity to check that we did set/ achieve
        # the intended value


class MercuryiPS(VisaInstrument):
    """
    Driver class for the QCoDeS Oxford Instruments MercuryiPS magnet power
    supply
    """

    def __init__(self, name: str, address: str, visalib=None,
                 field_limits: Optional[Callable]=None,
                 **kwargs) -> None:
        """
        Args:
            name: The name to give this instrument internally in QCoDeS
            address: The VISA resource of the instrument. Note that a
                socket connection to port 7020 must be made
            visalib: The VISA library to use. Leave blank if not in simulation
                mode.
            field_limits: A function describing the allowed field
                range (T). The function shall take (x, y, z) as an input and
                return a boolean describing whether that field value is
                acceptable.
        """

        if field_limits is not None and not(callable(field_limits)):
            raise ValueError('Got wrong type of field_limits. Must be a '
                             'function from (x, y, z) -> Bool. Received '
                             f'{type(field_limits)} instead.')

        if visalib:
            visabackend = visalib.split('@')[1]
        else:
            visabackend = 'NI'

        # ensure that a socket is used unless we are in simulation mode
        if not address.endswith('SOCKET') and visabackend != 'sim':
            raise ValueError('Incorrect VISA resource name. Must be of type '
                             'TCPIP0::XXX.XXX.XXX.XXX::7020::SOCKET.')

        super().__init__(name, address, terminator='\n', visalib=visalib,
                         **kwargs)

        # to ensure a correct snapshot, we must wrap the get function
        self.IDN.get = self.IDN._wrap_get(self._idn_getter)

        # TODO: Query instrument to ensure which PSUs are actually present
        for grp in ['GRPX', 'GRPY', 'GRPZ']:
            psu_name = grp
            psu = MercurySlavePS(self, psu_name, grp)
            self.add_submodule(psu_name, psu)

        self._field_limits = (field_limits if field_limits else
                              lambda x, y, z: True)

        self._target_vector = FieldVector(x=self.GRPX.field(),
                                          y=self.GRPY.field(),
                                          z=self.GRPZ.field())

        for coord in ['x', 'y', 'z', 'r', 'theta', 'phi', 'rho']:
            self.add_parameter(name=f'{coord}_target',
                               label=f'{coord.upper()} target field',
                               unit='T',
                               get_cmd=partial(self._get_component, coord),
                               set_cmd=partial(self._set_target, coord))

        self.connect_message()

    def _get_component(self, coordinate: str) -> float:
        return self._target_vector.get_components(coordinate)[0]

    def _set_target(self, coordinate: str, target: float) -> None:
        """
        The function to set a target value for a coordinate, i.e. the set_cmd
        for the XXX_target parameters
        """
        # first validate the new target
        valid_vec = FieldVector()
        valid_vec.copy(self._target_vector)
        valid_vec.set_component(**{coordinate: target})

        if not self._field_limits(*valid_vec.get_components('x', 'y', 'z')):
            raise ValueError(f'Cannot set {coordinate} target to {target}, '
                             'that would violate the field_limits. ')

        self._target_vector.set_component(**{coordinate: target})

    def _idn_getter(self) -> Dict[str, str]:
        """
        Parse the raw non-SCPI compliant IDN string into an IDN dict

        Returns:
            The normal IDN dict
        """
        raw_idn_string = self.ask('*IDN?')
        resps = raw_idn_string.split(':')

        idn_dict = {'model': resps[2], 'vendor': resps[1],
                    'serial': resps[3], 'firmware': resps[4]}

        # idn_string = ','.join([resps[2], resps[1], resps[3], resps[4]])

        return idn_dict

    def ask(self, cmd: str) -> str:
        """
        Since Oxford Instruments implement their own version of a SCPI-like
        language, we implement our own reader.

        Args:
            cmd: the command to send to the instrument
        """

        resp = self.visa_handle.ask(cmd)

        if 'INVALID' in resp:
            log.error('Invalid command. Got response: {}'.format(resp))
            base_resp = resp
        # if the command was not invalid, it can either be a SET or a READ
        # SET:
        elif resp.endswith('VALID'):
            base_resp = resp.split(':')[-2]
        # READ:
        else:
            # For "normal" commands only (e.g. '*IDN?' is excepted):
            # the response of a valid command echoes back said command,
            # thus we remove that part
            base_cmd = cmd.replace('READ:', '')
            base_resp = resp.replace('STAT:{}'.format(base_cmd), '')

        return base_resp
