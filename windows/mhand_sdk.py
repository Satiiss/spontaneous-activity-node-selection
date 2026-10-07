"""mHand data-read ABI, matching the existing GestureLoop adapter."""
from ctypes import CDLL, POINTER, Structure, c_bool, c_char_p, c_float, c_int, c_uint, c_ushort
from pathlib import Path


class MocapData(Structure):
    _fields_ = [('isUpdate', c_bool), ('frameIndex', c_uint), ('frequency', c_int), ('nsResult', c_int)]
    for _part, _count in (('body', 23), ('rHand', 20), ('lHand', 20)):
        _fields_ += [('sensorState_'+_part, c_int*_count)]
        for _name, _size in (('position', 3), ('quaternion', 4), ('gyr', 3), ('acc', 3), ('velocity', 3)):
            _fields_ += [(_name+'_'+_part, (c_float*_size)*_count)]
    _fields_ += [('isUseFaceBlendShapesARKit', c_bool), ('isUseFaceBlendShapesAudio', c_bool),
        ('faceBlendShapesARKit', c_float*52), ('faceBlendShapesAudio', c_float*26),
        ('localQuat_RightEyeball', c_float*4), ('localQuat_LeftEyeball', c_float*4),
        ('gestureResultL', c_int), ('gestureResultR', c_int)]


def load_sdk(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError('找不到 mHand SDK，请选择 VDMocapSDK_DataRead.dll')
    sdk = CDLL(str(path))
    for name, args, result in (
        ('UdpOpen', [c_int, c_ushort], c_bool), ('UdpClose', [c_int], None),
        ('UdpIsOpen', [c_int], c_bool),
        ('UdpSendRequestConnect', [c_int, c_char_p, c_ushort], c_bool),
        ('UdpRecvMocapData', [c_int, c_char_p, c_ushort, POINTER(MocapData)], c_bool),
        ('UdpRemove', [c_int, c_char_p, c_ushort], c_bool)):
        function = getattr(sdk, name)
        function.argtypes, function.restype = args, result
    return sdk
