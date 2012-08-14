import pycparser
from pycparserext.ext_c_parser import OpenCLCParser
from pycparserext.ext_c_generator import OpenCLCGenerator
import types
import re #For pattern matching preprocessor lines
import os #For getting the ACE_OCL_INCLUDES envvar.

################################################################################
#Checker
################################################################################
class TypeChecker(object):
    def __init__(self):
        self.parser = OpenCLCParser()
    
    def get_ast(self, code):
        ast = self.parser.parse(code)
        return ast
    
    def add_stmt_to_fn(self, ast, stmt_ast):
        ast.body.append(stmt_ast)

    def check_ast(self, ast, context):
        tc = OpenCLTypeChecker(context) #create checker w/ ctx
        return tc.visit(ast)
    
    def check(self, code, context):
        """Initiates a check, and throws an error on failure."""
        ast = self.parser.parse(code)
        tc = OpenCLTypeChecker(context) #create checker w/ new ctx
        return tc.visit(ast)

################################################################################
#C99 defintion
################################################################################

#General builtin function types. 
#These are used in ocl_builtins_generator as well.
GENTYPE_SIZES = sizes = ('','2','4','8','16')
GENTYPES   = list()
for t in ('int','uint','char','uchar','long','ulong','short','ushort'):
    for size in GENTYPE_SIZES:
        GENTYPES.append("%s%s" % (t,size))
SGENTYPES  = ('int','uint','char','uchar','long','ulong','short','ushort')
UGENTYPES  = ('uint','uchar','ulong','ushort')
IGENTYPES  = ('int','char','long','short')


class BuiltinFn(object):
    """ A built-in function."""
    def __init__(self,name,arg_list):
        """Constructor.
        
        name: name of function
        args: list of builtfntypelists
        return_type: string
        """
        self.args = list()
        self.name = name
        self.signatures = list() #list of lists
        self.signatures.append(arg_list)
    
    def add_arglist(self, builtin_arg_list):
        self.signatures.append(builtin_arg_list)
    
    def check(self, candidate_types):
        for arg_list in self.signatures:
            if arg_list.check(candidate_types):
                return True
        return False

    def return_type(self, candidate_types):
        """Determines the return_type of a function based upon the generic types
        of its arguments.
        
        For example, gentype->sgentype called with argument uint has return type
        int."""
        for arg_list in self.signatures:
            if arg_list.check(candidate_types):
                rt = arg_list.return_type.name
                if rt == "gentype" or rt == "sgentype" or rt == "igentype" or rt == "ugentype":
                    for (i,arg) in enumerate([arg.name for arg in arg_list.args]):
                        if arg == "gentype" or arg == "sgentype" or \
                        arg == "igentype" or arg == "ugentype":
                            return Type(BuiltinFnArgType.coerce(rt, candidate_types[i].name))
                    return None
                else:
                    return Type(rt)
    
    def __str__(self):
        ret =  self.name
        for arg_list in self.signatures:
            ret = "%s<%s>" % (ret, str(arg_list))
        return ret
            
                        
class BuiltinFnArgList(object):
    """A list of ``BuiltinFnArgTypes``.
    
    Consult \S 6.11 of the OpenCL specification for details.
    """
    def __init__(self, args, return_type):
        """Constructor
        
        args = list of ``BuiltinFnArgType``s
        return_type = ``BuiltinFnArgType``
        """
        self.args  = args
        self.return_type = return_type

    def check(self, candidates):
        """Returns true iff candidates is a valid set of parameters.
        
        candidates = list of ``Type``s.
        """
        if not len(self.args) == len(candidates):
            return False
        pairs = zip(self.args,candidates)
        for (arg,candidate) in pairs:
            if not arg.match(candidate.name): return False
        for (arg,c) in pairs:
            for (arg2,c2) in pairs:
                if not arg.corresponds(c,arg2,c2):
                    return False
        return True
        
    
    def __str__(self):
        t_names = ",".join([str(t) for t in self.args])
        ret = "(%s) %s %s" % \
        (t_names, "->", str(self.return_type))
        return ret
    
class BuiltinFnArgType(object):
    """A "type" for a built-in function (gentype, sgentype, etc.)"""
    def __init__(self, name, types):
        """Constructor.
        
        types = list of all Types that correspond to this arg type.
        Example: the gentype BuiltinFnArgType corresponds to int,int2,...
        """
        self.name  = name
        self.types = types
    
    @classmethod
    def coerce(cls, target_type_name, arg_t_name):
        """Returns the correct target_type_name based on an arg.
        
        Target_type_name = builtinfntype name
        arg = name of type of return arg.
        """
        if not isinstance(arg_t_name, str):
            raise TargetTypeCheckException("Expected str.",None)

        if target_type_name == "gentype":
            return arg_t_name
        elif target_type_name == "sgentype":
            return re.sub("\d+", "", arg_t_name)
        elif target_type_name == "ugentype":
            if not arg.index('u') == 0: arg = "u%s" % arg_t_name
            return re.sub("\d+", "", arg)
        elif target_type_name == "igentype":
            if arg_t_name.index('u') == 0: del arg_t_name[0]
            return re.sub("\d+", "", arg_t_name)
        else:
            return target_type_name

            
    def add_type(self, type):
        self.types.append(type)
    
    def match(self,arg_t):
        """Returns true iff arg type is one of the types in this arg type."""
        return arg_t in self.types 
    
    def corresponds(self, self_arg_t, type, arg_t):
        """Returns true iff there is a valid matching type.
        
        Returns true iff a BuiltinFnType ``self`` with argument type 
        ``self_arg_t`` is in correspondence with BuiltinFnType ``type`` 
        with argument type ``arg_t``. 
        
        See \S 6.11 of the OpenCL specification for specifics on behavior.
        
        Examples (based on \S 6.11):
            (gentype , "int", sgentype, "int")  -> True
            (ugentype, "uint", igentype, "int") -> True
            (ugentype, "uint", gentype, "short" -> False
            (ugentype, "uint", short, "short"   -> True
        """
        return self.unsymmetric_corresponds(self_arg_t, type, arg_t) or \
            type.unsymmetric_corresponds(arg_t, self, self_arg_t == None)

    def unsymmetric_corresponds(self, self_arg_t, type, arg_t):
        """A helper for ``corresponds``."""
        #gg? ss? uu? ii?
        if self.name == type.name:
            return self_arg_t == arg_t
        #gs? gu? gi? ui?
        elif self.name == "gentype":
            if type.name == "sgentype":
                if self_arg_t in SGENTYPES:
                    return self_arg_t == arg_t
                else:
                    s_self_arg_t = re.sub("\d+", "", self_arg_t)
                    return s_self_arg_t == arg_t
            elif type.name == "ugentype":
                if self.name in UGENTYPES:
                    return self_arg_t == arg_t
                else:
                    u_self_arg_t = "u%s" % self_arg_t
                    return u_self_arg_t == arg_t
            elif type.name == "igentype":
                if self.name in IGENTYPES:
                    return self_arg_t == arg_t
                else:
                    i_self_arg_t = re.sub("\d+", "", self_arg_t)
                    if i_self_arg_t.index("u") == 0:
                        del i_self_arg_t[0]
                    return i_self_arg_t == arg_t
            else:
                return True
        #su? si? 
        elif self.name == "sgentype":
            if type.name == "gentype": return False #unimpl
            if type.name == "ugentype":
                if self_arg_t in UGENTYPES:
                    return self_arg_t == arg_t
                else:
                    u_self_arg_t = "u%s" % self.name
                    return u_self_arg_t == arg_t
            if type.name == "igentype":
                if self_arg_t in IGENTYPES:
                    return self_arg_t == arg_t
                else:
                    i_self_arg_t = self_arg_t
                    del i_self_arg_t[0]
                    return i_self_arg_t == self_arg_t
            else:
                return True
        #ui?
        elif self.name == "ugentype":
            if type.name == "gentype" or type.name == "sgentype": 
                return False #unimpl
            if type.name == "igentype":
                #disjoint
                i_self_arg_t = self_arg_t
                del i_self_arg_t[0]
                return i_self_arg_t == arg_t
            else:
                return True
        else:
            return True  
    
    def __str__(self): 
        ret = "builtinfnargtype<" + self.name + ">"
        return ret

def c99fn(name,args=list(),return_type="void"):
    """Gets a FunctionType for a c99 built-in function"""
    #Create types out of argument names.
    arg_types = list()
    for arg in args:
        if arg.endswith("*"):
            arg.pop()
            t = Type(arg)
            t.is_ptr = True
            arg_types.append(t)
        else:
            arg_types.append(Type(arg))
    
    #Create type out of return type name
    return_type_t = None
    if return_type.endswith("*"):
        return_type.pop()
        return_type_t = Type(return_type)
        return_type_t.is_ptr = True
    else:
        return_type_t = Type(return_type)
        
    return FunctionType(name, arg_types, return_type_t)

c99_binops = ("+","-")
c99_conditional_ops = ("==","!=","<","<=",">",">=")
c99_unary_ops = ("-","!")

c99_op_pairs = {
    ('uchar', 'uchar'): 'uint',
    ('uchar', 'char'): 'uint',
    ('uchar', 'ushort'): 'uint',
    ('uchar', 'short'): 'uint',
    ('uchar', 'uint'): 'uint',
    ('uchar', 'int'): 'uint',
    ('uchar', 'ulong'): 'uint',
    ('uchar', 'long'): 'ulong',
    ('uchar', 'half'): 'float',
    ('uchar', 'float'): 'float',
    ('uchar', 'double'): 'double',
    ('uchar', 'uintptr_t'): 'uintptr_t',
    ('uchar', 'intptr_t'): 'uintptr_t',
    ('uchar', 'size_t'): 'size_t',
    ('uchar', 'ptrdiff_t'): 'size_t',
    
    ('char', 'uchar'): 'uint',
    ('char', 'char'): 'int',
    ('char', 'ushort'): 'uint',
    ('char', 'short'): 'int',
    ('char', 'uint'): 'uint',
    ('char', 'int'): 'int',
    ('char', 'ulong'): 'ulong',
    ('char', 'long'): 'long',
    ('char', 'half'): 'float',
    ('char', 'float'): 'float',
    ('char', 'double'): 'double',
    ('char', 'uintptr_t'): 'uintptr_t',
    ('char', 'intptr_t'): 'intptr_t',
    ('char', 'size_t'): 'size_t',
    ('char', 'ptrdiff_t'): 'ptrdiff_t',
    
    ('ushort', 'uchar'): 'uint',
    ('ushort', 'char'): 'uint',
    ('ushort', 'ushort'): 'uint',
    ('ushort', 'short'): 'uint',
    ('ushort', 'uint'): 'uint',
    ('ushort', 'int'): 'uint',
    ('ushort', 'ulong'): 'uint',
    ('ushort', 'long'): 'ulong',
    ('ushort', 'half'): 'float',
    ('ushort', 'float'): 'float',
    ('ushort', 'double'): 'double',
    ('ushort', 'uintptr_t'): 'uintptr_t',
    ('ushort', 'intptr_t'): 'uintptr_t',
    ('ushort', 'size_t'): 'size_t',
    ('ushort', 'ptrdiff_t'): 'size_t',
    
    ('short', 'uchar'): 'uint',
    ('short', 'char'): 'int',
    ('short', 'ushort'): 'uint',
    ('short', 'short'): 'int',
    ('short', 'uint'): 'uint',
    ('short', 'int'): 'int',
    ('short', 'ulong'): 'ulong',
    ('short', 'long'): 'long',
    ('short', 'half'): 'float',
    ('short', 'float'): 'float',
    ('short', 'double'): 'double',
    ('short', 'uintptr_t'): 'uintptr_t',
    ('short', 'intptr_t'): 'intptr_t',
    ('short', 'size_t'): 'size_t',
    ('short', 'ptrdiff_t'): 'ptrdiff_t',
    
    ('uint', 'uchar'): 'uint',
    ('uint', 'char'): 'uint',
    ('uint', 'ushort'): 'uint',
    ('uint', 'short'): 'uint',
    ('uint', 'uint'): 'uint',
    ('uint', 'int'): 'uint',
    ('uint', 'ulong'): 'ulong',
    ('uint', 'long'): 'ulong',
    ('uint', 'half'): 'float',
    ('uint', 'float'): 'float',
    ('uint', 'double'): 'double',
    ('uint', 'uintptr_t'): 'uintptr_t',
    ('uint', 'intptr_t'): 'uintptr_t',
    ('uint', 'size_t'): 'size_t',
    ('uint', 'ptrdiff_t'): 'size_t',
    
    ('int', 'uchar'): 'uint',
    ('int', 'char'): 'int',
    ('int', 'ushort'): 'uint',
    ('int', 'short'): 'int',
    ('int', 'uint'): 'uint',
    ('int', 'int'): 'int',
    ('int', 'ulong'): 'ulong',
    ('int', 'long'): 'long',
    ('int', 'half'): 'float',
    ('int', 'float'): 'float',
    ('int', 'double'): 'double',
    ('int', 'uintptr_t'): 'uintptr_t',
    ('int', 'intptr_t'): 'intptr_t',
    ('int', 'size_t'): 'size_t',
    ('int', 'ptrdiff_t'): 'ptrdiff_t',
    
    ('ulong', 'uchar'): 'ulong',
    ('ulong', 'char'): 'ulong',
    ('ulong', 'ushort'): 'ulong',
    ('ulong', 'short'): 'ulong',
    ('ulong', 'uint'): 'ulong',
    ('ulong', 'int'): 'ulong',
    ('ulong', 'ulong'): 'ulong',
    ('ulong', 'long'): 'ulong',
    ('ulong', 'half'): None,
    ('ulong', 'float'): 'float',
    ('ulong', 'double'): 'double',
    ('ulong', 'uintptr_t'): 'ulong',
    ('ulong', 'intptr_t'): 'ulong',
    ('ulong', 'size_t'): 'ulong',
    ('ulong', 'ptrdiff_t'): 'ulong',
    
    ('long', 'uchar'): 'ulong',
    ('long', 'char'): 'long',
    ('long', 'ushort'): 'ulong',
    ('long', 'short'): 'long',
    ('long', 'uint'): 'ulong',
    ('long', 'int'): 'long',
    ('long', 'ulong'): 'ulong',
    ('long', 'long'): 'long',
    ('long', 'half'): None,
    ('long', 'float'): 'float',
    ('long', 'double'): 'double',
    ('long', 'uintptr_t'): 'ulong',
    ('long', 'intptr_t'): 'long',
    ('long', 'size_t'): 'ulong',
    ('long', 'ptrdiff_t'): 'long',
    
    ('half', 'uchar'): 'float',
    ('half', 'char'): 'float',
    ('half', 'ushort'): 'float',
    ('half', 'short'): 'float',
    ('half', 'uint'): 'float',
    ('half', 'int'): 'float',
    ('half', 'ulong'): None,
    ('half', 'long'): None,
    ('half', 'half'): 'float',
    ('half', 'float'): 'float',
    ('half', 'double'): 'double',
    ('half', 'uintptr_t'): None,
    ('half', 'intptr_t'): None,
    ('half', 'size_t'): None,
    ('half', 'ptrdiff_t'): None,
    
    ('float', 'uchar'): 'float',
    ('float', 'char'): 'float',
    ('float', 'ushort'): 'float',
    ('float', 'short'): 'float',
    ('float', 'uint'): 'float',
    ('float', 'int'): 'float',
    ('float', 'ulong'): 'float',
    ('float', 'long'): 'float',
    ('float', 'half'): 'float',
    ('float', 'float'): 'float',
    ('float', 'double'): 'double',
    ('float', 'uintptr_t'): 'float',
    ('float', 'intptr_t'): 'float',
    ('float', 'size_t'): 'float',
    ('float', 'ptrdiff_t'): 'float',
        
    ('double', 'uchar'): 'double',
    ('double', 'char'): 'double',
    ('double', 'ushort'): 'double',
    ('double', 'short'): 'double',
    ('double', 'uint'): 'double',
    ('double', 'int'): 'double',
    ('double', 'ulong'): 'double',
    ('double', 'long'): 'double',
    ('double', 'half'): 'double',
    ('double', 'float'): 'double',
    ('double', 'double'): 'double',
    ('double', 'uintptr_t'): 'double',
    ('double', 'intptr_t'): 'double',
    ('double', 'size_t'): 'double',
    ('double', 'ptrdiff_t'): 'double',
    
    ('uintptr_t', 'uchar'): 'uintptr_t',
    ('uintptr_t', 'char'): 'uintptr_t',
    ('uintptr_t', 'ushort'): 'uintptr_t',
    ('uintptr_t', 'short'): 'uintptr_t',
    ('uintptr_t', 'uint'): 'uintptr_t',
    ('uintptr_t', 'int'): 'uintptr_t',
    ('uintptr_t', 'ulong'): 'ulong',
    ('uintptr_t', 'long'): 'ulong',
    ('uintptr_t', 'half'): None,
    ('uintptr_t', 'float'): 'float',
    ('uintptr_t', 'double'): 'double',
    ('uintptr_t', 'uintptr_t'): 'uintptr_t',
    ('uintptr_t', 'intptr_t'): 'uintptr_t',
    ('uintptr_t', 'size_t'): 'uintptr_t',   
    ('uintptr_t', 'ptrdiff_t'): 'uintptr_t',
    
    ('intptr_t', 'uchar'): 'uintptr_t',
    ('intptr_t', 'char'): 'intptr_t',
    ('intptr_t', 'ushort'): 'uintptr_t',
    ('intptr_t', 'short'): 'intptr_t',
    ('intptr_t', 'uint'): 'uintptr_t',
    ('intptr_t', 'int'): 'intptr_t',
    ('intptr_t', 'ulong'): 'ulong',
    ('intptr_t', 'long'): 'long',
    ('intptr_t', 'half'): None,
    ('intptr_t', 'float'): 'float',
    ('intptr_t', 'double'): 'double',
    ('intptr_t', 'uintptr_t'): 'uintptr_t',
    ('intptr_t', 'intptr_t'): 'intptr_t',
    ('intptr_t', 'size_t'): 'uintptr_t',
    ('intptr_t', 'ptrdiff_t'): 'intptr_t',
    
    ('size_t', 'uchar'): 'size_t',
    ('size_t', 'char'): 'size_t',
    ('size_t', 'ushort'): 'size_t',
    ('size_t', 'short'): 'size_t',
    ('size_t', 'uint'): 'size_t',
    ('size_t', 'int'): 'size_t',
    ('size_t', 'ulong'): 'ulong',
    ('size_t', 'long'): 'ulong',
    ('size_t', 'half'): None,
    ('size_t', 'float'): 'float',
    ('size_t', 'double'): 'double',
    ('size_t', 'uintptr_t'): 'uintptr_t',
    ('size_t', 'intptr_t'): 'uintptr_t',
    ('size_t', 'size_t'): 'size_t',   
    ('size_t', 'ptrdiff_t'): 'size_t',
    
    ('ptrdiff_t', 'uchar'): 'size_t',
    ('ptrdiff_t', 'char'): 'ptrdiff_t',
    ('ptrdiff_t', 'ushort'): 'size_t',
    ('ptrdiff_t', 'short'): 'ptrdiff_t',
    ('ptrdiff_t', 'uint'): 'size_t',
    ('ptrdiff_t', 'int'): 'ptrdiff_t',
    ('ptrdiff_t', 'ulong'): 'ulong',
    ('ptrdiff_t', 'long'): 'long',
    ('ptrdiff_t', 'half'): None,
    ('ptrdiff_t', 'float'): 'float',
    ('ptrdiff_t', 'double'): 'double',
    ('ptrdiff_t', 'uintptr_t'): 'uintptr_t',
    ('ptrdiff_t', 'intptr_t'): 'intptr_t',
    ('ptrdiff_t', 'size_t'): 'size_t',   
    ('ptrdiff_t', 'ptrdiff_t'): 'ptrdiff_t',
}

#Vector types
c99_scalar_types = ("uchar", "char",
         "ushort", "short",
         "uint", "int",
         "ulong", "long",
         "uintptr_t", "intptr_t",
         "size_t", "ptrdiff_t",
         "half", "float", "double",
         "void", "bool") 

vector_type_sizes = (2, 3, 4, 8, 16)

c99_vector_types = list()
for t in c99_scalar_types:
    for s in vector_type_sizes: c99_vector_types.append("%s%s"%(t,s))

#left can be substituted for right.
c99_substitutions = (
                     #char
#                     ('char', 'string'), #TODO
#                     ('string', 'char'), #TODO
                     ('char', 'uchar'),
                     ('char', 'short'),
                     ('char', 'ushort'),
                     ('char', 'int'),
                     ('char', 'uint'),
                     ('char', 'long'),
                     ('char', 'ulong'),
                     
                     #uchar
                     ('uchar', 'char'),
                     
                     #short
                     ('short', 'int'),
                     
                     #ushort
                     ('ushort','int'),
                     
                     #int
                     ('int', 'uint'),
                     ('int', 'size_t'),
                     ('int', 'long'),
                     ('int', 'char'),
                     ('int', 'uchar'),
                     ('int', 'short'),
                     ('int', 'ushort'),
                     ('int', 'uint'),
                     ('int', 'long'),
                     ('int', 'ulong'),
                     
                     #uint
                     ('uint','int'),
                     
                     #long
                     ('long', 'ulong'),
                     ('long', 'int'),
                     #ulong
                     ('ulong','long'),
                     ('ulong','int'),
                     
                     #size_t
                     ('size_t', 'int'),
                    )
def transitive_sub_r(given,expected,intermediate,tested):
    """Use trensitive_sub
    
    intermediate = the current left hand size.
    
    tested = a list of values that have already been attempted, and is used for
    cycle avoidance.
    """
    if given == None or intermediate == None: return False
    if intermediate == None: intermediate = given
    tested.append(intermediate)
    for s in c99_substitutions:
        if s[0] == intermediate:
            if s[1] == expected: return True
            if not s[1] in tested:
                if transitive_sub_r(given,expected,s[1],tested):
                    return True
    return False

def transitive_sub(given,expected):
    """Determines if there's a path from given to expected in c99_substitutions.
    """
    return transitive_sub_r(given, expected, expected, list())


################################################################################
#            GENERATED CODE FOR BUILTIN FUNCTIONS -- See ocl_builtins_generator#
################################################################################
### BEGIN GENERATED CODE ###
#Parameters
builtinfnargtype_0 = BuiltinFnArgType("uint",("uint"))
builtinfnargtype_1 = BuiltinFnArgType("size_t",("size_t"))
builtinfnargtype_2 = BuiltinFnArgType("gentype",("int","int2","int4","int8","int16","uint","uint2","uint4","uint8","uint16","char","char2","char4","char8","char16","uchar","uchar2","uchar4","uchar8","uchar16","long","long2","long4","long8","long16","ulong","ulong2","ulong4","ulong8","ulong16","short","short2","short4","short8","short16","ushort","ushort2","ushort4","ushort8","ushort16"))
builtinfnargtype_3 = BuiltinFnArgType("float",("float"))
builtinfnargtype_4 = BuiltinFnArgType("int",("int"))
builtinfnargtype_5 = BuiltinFnArgType("float2",("float2"))
builtinfnargtype_6 = BuiltinFnArgType("float4",("float4"))
builtinfnargtype_7 = BuiltinFnArgType("float8",("float8"))
builtinfnargtype_8 = BuiltinFnArgType("float16",("float16"))
builtinfnargtype_9 = BuiltinFnArgType("int2",("int2"))
builtinfnargtype_10 = BuiltinFnArgType("int4",("int4"))
builtinfnargtype_11 = BuiltinFnArgType("int8",("int8"))
builtinfnargtype_12 = BuiltinFnArgType("int16",("int16"))
builtinfnargtype_13 = BuiltinFnArgType("unit",("unit"))
builtinfnargtype_14 = BuiltinFnArgType("unit2",("unit2"))
builtinfnargtype_15 = BuiltinFnArgType("unit4",("unit4"))
builtinfnargtype_16 = BuiltinFnArgType("unit8",("unit8"))
builtinfnargtype_17 = BuiltinFnArgType("unit16",("unit16"))
builtinfnargtype_18 = BuiltinFnArgType("ugentype",("int","int2","int4","int8","int16","uint","uint2","uint4","uint8","uint16","char","char2","char4","char8","char16","uchar","uchar2","uchar4","uchar8","uchar16","long","long2","long4","long8","long16","ulong","ulong2","ulong4","ulong8","ulong16","short","short2","short4","short8","short16","ushort","ushort2","ushort4","ushort8","ushort16"))
builtinfnargtype_19 = BuiltinFnArgType("sgentype",("int","uint","char","uchar","long","ulong","short","ushort"))
builtinfnargtype_20 = BuiltinFnArgType("char",("char"))
builtinfnargtype_21 = BuiltinFnArgType("uchar",("uchar"))
builtinfnargtype_22 = BuiltinFnArgType("short",("short"))
builtinfnargtype_23 = BuiltinFnArgType("char2",("char2"))
builtinfnargtype_24 = BuiltinFnArgType("uchar2",("uchar2"))
builtinfnargtype_25 = BuiltinFnArgType("short2",("short2"))
builtinfnargtype_26 = BuiltinFnArgType("char4",("char4"))
builtinfnargtype_27 = BuiltinFnArgType("uchar4",("uchar4"))
builtinfnargtype_28 = BuiltinFnArgType("short4",("short4"))
builtinfnargtype_29 = BuiltinFnArgType("char8",("char8"))
builtinfnargtype_30 = BuiltinFnArgType("uchar8",("uchar8"))
builtinfnargtype_31 = BuiltinFnArgType("short8",("short8"))
builtinfnargtype_32 = BuiltinFnArgType("char16",("char16"))
builtinfnargtype_33 = BuiltinFnArgType("uchar16",("uchar16"))
builtinfnargtype_34 = BuiltinFnArgType("short16",("short16"))
builtinfnargtype_35 = BuiltinFnArgType("ushort",("ushort"))
builtinfnargtype_36 = BuiltinFnArgType("ushort2",("ushort2"))
builtinfnargtype_37 = BuiltinFnArgType("ushort4",("ushort4"))
builtinfnargtype_38 = BuiltinFnArgType("ushort8",("ushort8"))
builtinfnargtype_39 = BuiltinFnArgType("ushort16",("ushort16"))
builtinfnargtype_40 = BuiltinFnArgType("uint2",("uint2"))
builtinfnargtype_41 = BuiltinFnArgType("uint4",("uint4"))
builtinfnargtype_42 = BuiltinFnArgType("uint8",("uint8"))
builtinfnargtype_43 = BuiltinFnArgType("uint16",("uint16"))
builtinfnargtype_44 = BuiltinFnArgType("long",("long"))
builtinfnargtype_45 = BuiltinFnArgType("long2",("long2"))
builtinfnargtype_46 = BuiltinFnArgType("long4",("long4"))
builtinfnargtype_47 = BuiltinFnArgType("long8",("long8"))
builtinfnargtype_48 = BuiltinFnArgType("long16",("long16"))
builtinfnargtype_49 = BuiltinFnArgType("ulong",("ulong"))
builtinfnargtype_50 = BuiltinFnArgType("ulong2",("ulong2"))
builtinfnargtype_51 = BuiltinFnArgType("ulong4",("ulong4"))
builtinfnargtype_52 = BuiltinFnArgType("ulong8",("ulong8"))
builtinfnargtype_53 = BuiltinFnArgType("ulong16",("ulong16"))
builtinfnargtype_54 = BuiltinFnArgType("float3",("float3"))
builtinfnargtype_55 = BuiltinFnArgType("gentype2",("gentype2"))
builtinfnargtype_56 = BuiltinFnArgType("gentype4",("gentype4"))
builtinfnargtype_57 = BuiltinFnArgType("gentype8",("gentype8"))
builtinfnargtype_58 = BuiltinFnArgType("gentype16",("gentype16"))
builtinfnargtype_59 = BuiltinFnArgType("float",("float"))
builtinfnargtype_59.is_ptr = True

#Parameter Lists
builtinfnarglist_0=BuiltinFnArgList((),builtinfnargtype_0)
builtinfnarglist_1=BuiltinFnArgList((builtinfnargtype_0,),builtinfnargtype_1)
builtinfnarglist_8=BuiltinFnArgList((builtinfnargtype_2,),builtinfnargtype_2)
builtinfnarglist_12=BuiltinFnArgList((builtinfnargtype_2,builtinfnargtype_2,),builtinfnargtype_2)
builtinfnarglist_31=BuiltinFnArgList((builtinfnargtype_2,builtinfnargtype_2,builtinfnargtype_2,),builtinfnargtype_2)
builtinfnarglist_33=BuiltinFnArgList((builtinfnargtype_2,builtinfnargtype_3,),builtinfnargtype_2)
builtinfnarglist_38=BuiltinFnArgList((builtinfnargtype_4,),builtinfnargtype_3)
builtinfnarglist_43=BuiltinFnArgList((builtinfnargtype_3,builtinfnargtype_4,),builtinfnargtype_3)
builtinfnarglist_44=BuiltinFnArgList((builtinfnargtype_5,builtinfnargtype_9,),builtinfnargtype_5)
builtinfnarglist_45=BuiltinFnArgList((builtinfnargtype_6,builtinfnargtype_10,),builtinfnargtype_6)
builtinfnarglist_46=BuiltinFnArgList((builtinfnargtype_7,builtinfnargtype_11,),builtinfnargtype_7)
builtinfnarglist_47=BuiltinFnArgList((builtinfnargtype_8,builtinfnargtype_12,),builtinfnargtype_8)
builtinfnarglist_57=BuiltinFnArgList((builtinfnargtype_13,),builtinfnargtype_3)
builtinfnarglist_58=BuiltinFnArgList((builtinfnargtype_14,),builtinfnargtype_5)
builtinfnarglist_59=BuiltinFnArgList((builtinfnargtype_15,),builtinfnargtype_6)
builtinfnarglist_60=BuiltinFnArgList((builtinfnargtype_16,),builtinfnargtype_7)
builtinfnarglist_61=BuiltinFnArgList((builtinfnargtype_17,),builtinfnargtype_8)
builtinfnarglist_123=BuiltinFnArgList((builtinfnargtype_2,builtinfnargtype_19,),builtinfnargtype_2)
builtinfnarglist_129=BuiltinFnArgList((builtinfnargtype_20,builtinfnargtype_21,),builtinfnargtype_22)
builtinfnarglist_130=BuiltinFnArgList((builtinfnargtype_23,builtinfnargtype_24,),builtinfnargtype_25)
builtinfnarglist_131=BuiltinFnArgList((builtinfnargtype_26,builtinfnargtype_27,),builtinfnargtype_28)
builtinfnarglist_132=BuiltinFnArgList((builtinfnargtype_29,builtinfnargtype_30,),builtinfnargtype_31)
builtinfnarglist_133=BuiltinFnArgList((builtinfnargtype_32,builtinfnargtype_33,),builtinfnargtype_34)
builtinfnarglist_134=BuiltinFnArgList((builtinfnargtype_21,builtinfnargtype_21,),builtinfnargtype_35)
builtinfnarglist_135=BuiltinFnArgList((builtinfnargtype_24,builtinfnargtype_24,),builtinfnargtype_36)
builtinfnarglist_136=BuiltinFnArgList((builtinfnargtype_27,builtinfnargtype_27,),builtinfnargtype_37)
builtinfnarglist_137=BuiltinFnArgList((builtinfnargtype_30,builtinfnargtype_30,),builtinfnargtype_38)
builtinfnarglist_138=BuiltinFnArgList((builtinfnargtype_33,builtinfnargtype_33,),builtinfnargtype_39)
builtinfnarglist_162=BuiltinFnArgList((builtinfnargtype_2,builtinfnargtype_3,builtinfnargtype_3,),builtinfnargtype_2)
builtinfnarglist_172=BuiltinFnArgList((builtinfnargtype_3,builtinfnargtype_2,),builtinfnargtype_2)
builtinfnarglist_174=BuiltinFnArgList((builtinfnargtype_3,builtinfnargtype_3,builtinfnargtype_2,),builtinfnargtype_2)
builtinfnarglist_176=BuiltinFnArgList((builtinfnargtype_6,builtinfnargtype_6,),builtinfnargtype_6)
builtinfnarglist_177=BuiltinFnArgList((builtinfnargtype_54,builtinfnargtype_54,),builtinfnargtype_54)
builtinfnarglist_178=BuiltinFnArgList((builtinfnargtype_3,builtinfnargtype_3,),builtinfnargtype_3)
builtinfnarglist_179=BuiltinFnArgList((builtinfnargtype_3,builtinfnargtype_5,),builtinfnargtype_3)
builtinfnarglist_180=BuiltinFnArgList((builtinfnargtype_3,builtinfnargtype_54,),builtinfnargtype_3)
builtinfnarglist_181=BuiltinFnArgList((builtinfnargtype_3,builtinfnargtype_6,),builtinfnargtype_3)
builtinfnarglist_183=BuiltinFnArgList((builtinfnargtype_5,builtinfnargtype_5,),builtinfnargtype_3)
builtinfnarglist_186=BuiltinFnArgList((builtinfnargtype_3,),builtinfnargtype_3)
builtinfnarglist_187=BuiltinFnArgList((builtinfnargtype_5,),builtinfnargtype_3)
builtinfnarglist_188=BuiltinFnArgList((builtinfnargtype_54,),builtinfnargtype_3)
builtinfnarglist_189=BuiltinFnArgList((builtinfnargtype_6,),builtinfnargtype_3)
builtinfnarglist_206=BuiltinFnArgList((builtinfnargtype_4,builtinfnargtype_3,),builtinfnargtype_4)
builtinfnarglist_207=BuiltinFnArgList((builtinfnargtype_9,builtinfnargtype_5,),builtinfnargtype_9)
builtinfnarglist_208=BuiltinFnArgList((builtinfnargtype_10,builtinfnargtype_6,),builtinfnargtype_10)
builtinfnarglist_209=BuiltinFnArgList((builtinfnargtype_11,builtinfnargtype_7,),builtinfnargtype_11)
builtinfnarglist_210=BuiltinFnArgList((builtinfnargtype_12,builtinfnargtype_8,),builtinfnargtype_12)
builtinfnarglist_234=BuiltinFnArgList((builtinfnargtype_7,),builtinfnargtype_11)
builtinfnarglist_235=BuiltinFnArgList((builtinfnargtype_8,),builtinfnargtype_12)
builtinfnarglist_254=BuiltinFnArgList((builtinfnargtype_7,builtinfnargtype_7,),builtinfnargtype_11)
builtinfnarglist_255=BuiltinFnArgList((builtinfnargtype_8,builtinfnargtype_8,),builtinfnargtype_12)
builtinfnarglist_257=BuiltinFnArgList((builtinfnargtype_9,),builtinfnargtype_9)
builtinfnarglist_258=BuiltinFnArgList((builtinfnargtype_10,),builtinfnargtype_10)
builtinfnarglist_259=BuiltinFnArgList((builtinfnargtype_11,),builtinfnargtype_11)
builtinfnarglist_260=BuiltinFnArgList((builtinfnargtype_12,),builtinfnargtype_12)
builtinfnarglist_265=BuiltinFnArgList((builtinfnargtype_1,builtinfnargtype_55,),builtinfnargtype_55)
builtinfnarglist_266=BuiltinFnArgList((builtinfnargtype_1,builtinfnargtype_56,),builtinfnargtype_56)
builtinfnarglist_267=BuiltinFnArgList((builtinfnargtype_1,builtinfnargtype_57,),builtinfnargtype_57)
builtinfnarglist_268=BuiltinFnArgList((builtinfnargtype_1,builtinfnargtype_58,),builtinfnargtype_58)
builtinfnarglist_269=BuiltinFnArgList((builtinfnargtype_55,builtinfnargtype_1,builtinfnargtype_55,),builtinfnargtype_55)
builtinfnarglist_270=BuiltinFnArgList((builtinfnargtype_56,builtinfnargtype_1,builtinfnargtype_56,),builtinfnargtype_56)
builtinfnarglist_271=BuiltinFnArgList((builtinfnargtype_57,builtinfnargtype_1,builtinfnargtype_57,),builtinfnargtype_57)
builtinfnarglist_272=BuiltinFnArgList((builtinfnargtype_58,builtinfnargtype_1,builtinfnargtype_58,),builtinfnargtype_58)
builtinfnarglist_273=BuiltinFnArgList((builtinfnargtype_1,builtinfnargtype_59,),builtinfnargtype_3)

#functions
builtin_fns = dict()
builtin_fns["get_work_dim"] = BuiltinFn("get_work_dim",builtinfnarglist_0)
builtin_fns["get_global_size"] = BuiltinFn("get_global_size",builtinfnarglist_1)
builtin_fns["get_global_id"] = BuiltinFn("get_global_id",builtinfnarglist_1)
builtin_fns["get_local_size"] = BuiltinFn("get_local_size",builtinfnarglist_1)
builtin_fns["get_local_id"] = BuiltinFn("get_local_id",builtinfnarglist_1)
builtin_fns["get_num_groups"] = BuiltinFn("get_num_groups",builtinfnarglist_1)
builtin_fns["get_group_id"] = BuiltinFn("get_group_id",builtinfnarglist_1)
builtin_fns["get_global_offset"] = BuiltinFn("get_global_offset",builtinfnarglist_1)
builtin_fns["acos"] = BuiltinFn("acos",builtinfnarglist_8)
builtin_fns["acosh"] = BuiltinFn("acosh",builtinfnarglist_8)
builtin_fns["acospi"] = BuiltinFn("acospi",builtinfnarglist_8)
builtin_fns["atan"] = BuiltinFn("atan",builtinfnarglist_8)
builtin_fns["atan2"] = BuiltinFn("atan2",builtinfnarglist_12)
builtin_fns["atanh"] = BuiltinFn("atanh",builtinfnarglist_8)
builtin_fns["atanpi"] = BuiltinFn("atanpi",builtinfnarglist_8)
builtin_fns["atan2pi"] = BuiltinFn("atan2pi",builtinfnarglist_12)
builtin_fns["cbry"] = BuiltinFn("cbry",builtinfnarglist_8)
builtin_fns["ceil"] = BuiltinFn("ceil",builtinfnarglist_8)
builtin_fns["copysign"] = BuiltinFn("copysign",builtinfnarglist_12)
builtin_fns["cos"] = BuiltinFn("cos",builtinfnarglist_8)
builtin_fns["cosh"] = BuiltinFn("cosh",builtinfnarglist_8)
builtin_fns["cospi"] = BuiltinFn("cospi",builtinfnarglist_8)
builtin_fns["erfc"] = BuiltinFn("erfc",builtinfnarglist_8)
builtin_fns["erf"] = BuiltinFn("erf",builtinfnarglist_8)
builtin_fns["exp"] = BuiltinFn("exp",builtinfnarglist_8)
builtin_fns["exp2"] = BuiltinFn("exp2",builtinfnarglist_12)
builtin_fns["exp10"] = BuiltinFn("exp10",builtinfnarglist_8)
builtin_fns["expm1"] = BuiltinFn("expm1",builtinfnarglist_8)
builtin_fns["fabs"] = BuiltinFn("fabs",builtinfnarglist_8)
builtin_fns["fdim"] = BuiltinFn("fdim",builtinfnarglist_12)
builtin_fns["floor"] = BuiltinFn("floor",builtinfnarglist_8)
builtin_fns["fma"] = BuiltinFn("fma",builtinfnarglist_31)
builtin_fns["fmax"] = BuiltinFn("fmax",builtinfnarglist_12)
builtin_fns["fmax"].signatures.append(builtinfnarglist_33)
builtin_fns["fmin"] = BuiltinFn("fmin",builtinfnarglist_12)
builtin_fns["fmin"].signatures.append(builtinfnarglist_33)
builtin_fns["fmod"] = BuiltinFn("fmod",builtinfnarglist_12)
builtin_fns["hypo"] = BuiltinFn("hypo",builtinfnarglist_12)
builtin_fns["ilogb"] = BuiltinFn("ilogb",builtinfnarglist_38)
builtin_fns["ilogb2"] = BuiltinFn("ilogb2",builtinfnarglist_38)
builtin_fns["ilogb4"] = BuiltinFn("ilogb4",builtinfnarglist_38)
builtin_fns["ilogb8"] = BuiltinFn("ilogb8",builtinfnarglist_38)
builtin_fns["ilogb16"] = BuiltinFn("ilogb16",builtinfnarglist_38)
builtin_fns["ldexp"] = BuiltinFn("ldexp",builtinfnarglist_43)
builtin_fns["ldexp"].signatures.append(builtinfnarglist_44)
builtin_fns["ldexp"].signatures.append(builtinfnarglist_45)
builtin_fns["ldexp"].signatures.append(builtinfnarglist_46)
builtin_fns["ldexp"].signatures.append(builtinfnarglist_47)
builtin_fns["lgamma"] = BuiltinFn("lgamma",builtinfnarglist_8)
builtin_fns["log"] = BuiltinFn("log",builtinfnarglist_8)
builtin_fns["log2"] = BuiltinFn("log2",builtinfnarglist_8)
builtin_fns["log10"] = BuiltinFn("log10",builtinfnarglist_8)
builtin_fns["log1p"] = BuiltinFn("log1p",builtinfnarglist_8)
builtin_fns["logb"] = BuiltinFn("logb",builtinfnarglist_8)
builtin_fns["mad"] = BuiltinFn("mad",builtinfnarglist_8)
builtin_fns["maxmag"] = BuiltinFn("maxmag",builtinfnarglist_12)
builtin_fns["minmag"] = BuiltinFn("minmag",builtinfnarglist_12)
builtin_fns["nan"] = BuiltinFn("nan",builtinfnarglist_57)
builtin_fns["nan"].signatures.append(builtinfnarglist_58)
builtin_fns["nan"].signatures.append(builtinfnarglist_59)
builtin_fns["nan"].signatures.append(builtinfnarglist_60)
builtin_fns["nan"].signatures.append(builtinfnarglist_61)
builtin_fns["nextafter"] = BuiltinFn("nextafter",builtinfnarglist_12)
builtin_fns["pow"] = BuiltinFn("pow",builtinfnarglist_12)
builtin_fns["pown"] = BuiltinFn("pown",builtinfnarglist_43)
builtin_fns["pown"].signatures.append(builtinfnarglist_44)
builtin_fns["pown"].signatures.append(builtinfnarglist_45)
builtin_fns["pown"].signatures.append(builtinfnarglist_46)
builtin_fns["pown"].signatures.append(builtinfnarglist_47)
builtin_fns["powr"] = BuiltinFn("powr",builtinfnarglist_12)
builtin_fns["rint"] = BuiltinFn("rint",builtinfnarglist_8)
builtin_fns["rootn"] = BuiltinFn("rootn",builtinfnarglist_43)
builtin_fns["rootn"].signatures.append(builtinfnarglist_44)
builtin_fns["rootn"].signatures.append(builtinfnarglist_45)
builtin_fns["rootn"].signatures.append(builtinfnarglist_46)
builtin_fns["rootn"].signatures.append(builtinfnarglist_47)
builtin_fns["round"] = BuiltinFn("round",builtinfnarglist_8)
builtin_fns["rsqrt"] = BuiltinFn("rsqrt",builtinfnarglist_8)
builtin_fns["sin"] = BuiltinFn("sin",builtinfnarglist_8)
builtin_fns["sinh"] = BuiltinFn("sinh",builtinfnarglist_8)
builtin_fns["sinpi"] = BuiltinFn("sinpi",builtinfnarglist_8)
builtin_fns["sqrt"] = BuiltinFn("sqrt",builtinfnarglist_8)
builtin_fns["tan"] = BuiltinFn("tan",builtinfnarglist_8)
builtin_fns["tanh"] = BuiltinFn("tanh",builtinfnarglist_8)
builtin_fns["tanpi"] = BuiltinFn("tanpi",builtinfnarglist_8)
builtin_fns["tgamma"] = BuiltinFn("tgamma",builtinfnarglist_8)
builtin_fns["tunc"] = BuiltinFn("tunc",builtinfnarglist_8)
builtin_fns["half_cos"] = BuiltinFn("half_cos",builtinfnarglist_8)
builtin_fns["half_divide"] = BuiltinFn("half_divide",builtinfnarglist_8)
builtin_fns["half_exp"] = BuiltinFn("half_exp",builtinfnarglist_8)
builtin_fns["half_exp10"] = BuiltinFn("half_exp10",builtinfnarglist_8)
builtin_fns["half_log"] = BuiltinFn("half_log",builtinfnarglist_8)
builtin_fns["half_log2"] = BuiltinFn("half_log2",builtinfnarglist_8)
builtin_fns["half_log10"] = BuiltinFn("half_log10",builtinfnarglist_8)
builtin_fns["half_powr"] = BuiltinFn("half_powr",builtinfnarglist_12)
builtin_fns["half_recip"] = BuiltinFn("half_recip",builtinfnarglist_8)
builtin_fns["half_rsqrt"] = BuiltinFn("half_rsqrt",builtinfnarglist_8)
builtin_fns["half_sin"] = BuiltinFn("half_sin",builtinfnarglist_8)
builtin_fns["half_sqrt"] = BuiltinFn("half_sqrt",builtinfnarglist_8)
builtin_fns["half_tan"] = BuiltinFn("half_tan",builtinfnarglist_8)
builtin_fns["native_cos"] = BuiltinFn("native_cos",builtinfnarglist_8)
builtin_fns["native_divide"] = BuiltinFn("native_divide",builtinfnarglist_8)
builtin_fns["native_exp"] = BuiltinFn("native_exp",builtinfnarglist_8)
builtin_fns["native_exp10"] = BuiltinFn("native_exp10",builtinfnarglist_8)
builtin_fns["native_log"] = BuiltinFn("native_log",builtinfnarglist_8)
builtin_fns["native_log2"] = BuiltinFn("native_log2",builtinfnarglist_8)
builtin_fns["native_log10"] = BuiltinFn("native_log10",builtinfnarglist_8)
builtin_fns["native_powr"] = BuiltinFn("native_powr",builtinfnarglist_12)
builtin_fns["native_recip"] = BuiltinFn("native_recip",builtinfnarglist_8)
builtin_fns["native_rsqrt"] = BuiltinFn("native_rsqrt",builtinfnarglist_8)
builtin_fns["native_sin"] = BuiltinFn("native_sin",builtinfnarglist_8)
builtin_fns["native_sqrt"] = BuiltinFn("native_sqrt",builtinfnarglist_8)
builtin_fns["native_tan"] = BuiltinFn("native_tan",builtinfnarglist_8)
builtin_fns["abs"] = BuiltinFn("abs",builtinfnarglist_8)
builtin_fns["abs_diff"] = BuiltinFn("abs_diff",builtinfnarglist_12)
builtin_fns["add_sat"] = BuiltinFn("add_sat",builtinfnarglist_12)
builtin_fns["hadd"] = BuiltinFn("hadd",builtinfnarglist_12)
builtin_fns["rhadd"] = BuiltinFn("rhadd",builtinfnarglist_12)
builtin_fns["clamp"] = BuiltinFn("clamp",builtinfnarglist_31)
builtin_fns["clq"] = BuiltinFn("clq",builtinfnarglist_8)
builtin_fns["mad_hi"] = BuiltinFn("mad_hi",builtinfnarglist_31)
builtin_fns["mad_sat"] = BuiltinFn("mad_sat",builtinfnarglist_31)
builtin_fns["max"] = BuiltinFn("max",builtinfnarglist_12)
builtin_fns["max"].signatures.append(builtinfnarglist_123)
builtin_fns["min"] = BuiltinFn("min",builtinfnarglist_12)
builtin_fns["min"].signatures.append(builtinfnarglist_123)
builtin_fns["mul_hi"] = BuiltinFn("mul_hi",builtinfnarglist_12)
builtin_fns["rotate"] = BuiltinFn("rotate",builtinfnarglist_12)
builtin_fns["subsat"] = BuiltinFn("subsat",builtinfnarglist_12)
builtin_fns["upsample"] = BuiltinFn("upsample",builtinfnarglist_129)
builtin_fns["upsample"].signatures.append(builtinfnarglist_130)
builtin_fns["upsample"].signatures.append(builtinfnarglist_131)
builtin_fns["upsample"].signatures.append(builtinfnarglist_132)
builtin_fns["upsample"].signatures.append(builtinfnarglist_133)
builtin_fns["upsample"].signatures.append(builtinfnarglist_134)
builtin_fns["upsample"].signatures.append(builtinfnarglist_135)
builtin_fns["upsample"].signatures.append(builtinfnarglist_136)
builtin_fns["upsample"].signatures.append(builtinfnarglist_137)
builtin_fns["upsample"].signatures.append(builtinfnarglist_138)
builtin_fns["upsample"].signatures.append(builtinfnarglist_129)
builtin_fns["upsample"].signatures.append(builtinfnarglist_130)
builtin_fns["upsample"].signatures.append(builtinfnarglist_131)
builtin_fns["upsample"].signatures.append(builtinfnarglist_132)
builtin_fns["upsample"].signatures.append(builtinfnarglist_133)
builtin_fns["upsample"].signatures.append(builtinfnarglist_134)
builtin_fns["upsample"].signatures.append(builtinfnarglist_135)
builtin_fns["upsample"].signatures.append(builtinfnarglist_136)
builtin_fns["upsample"].signatures.append(builtinfnarglist_137)
builtin_fns["upsample"].signatures.append(builtinfnarglist_138)
builtin_fns["upsample"].signatures.append(builtinfnarglist_129)
builtin_fns["upsample"].signatures.append(builtinfnarglist_130)
builtin_fns["upsample"].signatures.append(builtinfnarglist_131)
builtin_fns["upsample"].signatures.append(builtinfnarglist_132)
builtin_fns["upsample"].signatures.append(builtinfnarglist_133)
builtin_fns["upsample"].signatures.append(builtinfnarglist_134)
builtin_fns["upsample"].signatures.append(builtinfnarglist_135)
builtin_fns["upsample"].signatures.append(builtinfnarglist_136)
builtin_fns["upsample"].signatures.append(builtinfnarglist_137)
builtin_fns["upsample"].signatures.append(builtinfnarglist_138)
builtin_fns["mad24"] = BuiltinFn("mad24",builtinfnarglist_31)
builtin_fns["mul24"] = BuiltinFn("mul24",builtinfnarglist_12)
builtin_fns["clamp"].signatures.append(builtinfnarglist_31)
builtin_fns["clamp"].signatures.append(builtinfnarglist_162)
builtin_fns["degrees"] = BuiltinFn("degrees",builtinfnarglist_8)
builtin_fns["max"].signatures.append(builtinfnarglist_12)
builtin_fns["max"].signatures.append(builtinfnarglist_33)
builtin_fns["min"].signatures.append(builtinfnarglist_12)
builtin_fns["min"].signatures.append(builtinfnarglist_33)
builtin_fns["mix"] = BuiltinFn("mix",builtinfnarglist_31)
builtin_fns["mix"].signatures.append(builtinfnarglist_31)
builtin_fns["randian"] = BuiltinFn("randian",builtinfnarglist_8)
builtin_fns["step"] = BuiltinFn("step",builtinfnarglist_12)
builtin_fns["step"].signatures.append(builtinfnarglist_172)
builtin_fns["smoothstep"] = BuiltinFn("smoothstep",builtinfnarglist_31)
builtin_fns["smoothetype"] = BuiltinFn("smoothetype",builtinfnarglist_174)
builtin_fns["sign"] = BuiltinFn("sign",builtinfnarglist_8)
builtin_fns["cross"] = BuiltinFn("cross",builtinfnarglist_176)
builtin_fns["cross"].signatures.append(builtinfnarglist_177)
builtin_fns["dot"] = BuiltinFn("dot",builtinfnarglist_178)
builtin_fns["dot"].signatures.append(builtinfnarglist_179)
builtin_fns["dot"].signatures.append(builtinfnarglist_180)
builtin_fns["dot"].signatures.append(builtinfnarglist_181)
builtin_fns["distance"] = BuiltinFn("distance",builtinfnarglist_178)
builtin_fns["distance"].signatures.append(builtinfnarglist_183)
builtin_fns["distance"].signatures.append(builtinfnarglist_177)
builtin_fns["distance"].signatures.append(builtinfnarglist_176)
builtin_fns["length"] = BuiltinFn("length",builtinfnarglist_186)
builtin_fns["length"].signatures.append(builtinfnarglist_187)
builtin_fns["length"].signatures.append(builtinfnarglist_188)
builtin_fns["length"].signatures.append(builtinfnarglist_189)
builtin_fns["normalize"] = BuiltinFn("normalize",builtinfnarglist_186)
builtin_fns["normalize"].signatures.append(builtinfnarglist_187)
builtin_fns["normalize"].signatures.append(builtinfnarglist_188)
builtin_fns["normalize"].signatures.append(builtinfnarglist_189)
builtin_fns["fast_distance"] = BuiltinFn("fast_distance",builtinfnarglist_186)
builtin_fns["fast_distance"].signatures.append(builtinfnarglist_187)
builtin_fns["fast_distance"].signatures.append(builtinfnarglist_188)
builtin_fns["fast_distance"].signatures.append(builtinfnarglist_189)
builtin_fns["fast_length"] = BuiltinFn("fast_length",builtinfnarglist_186)
builtin_fns["fast_length"].signatures.append(builtinfnarglist_187)
builtin_fns["fast_length"].signatures.append(builtinfnarglist_188)
builtin_fns["fast_length"].signatures.append(builtinfnarglist_189)
builtin_fns["fast_normalize"] = BuiltinFn("fast_normalize",builtinfnarglist_186)
builtin_fns["fast_normalize"].signatures.append(builtinfnarglist_187)
builtin_fns["fast_normalize"].signatures.append(builtinfnarglist_188)
builtin_fns["fast_normalize"].signatures.append(builtinfnarglist_189)
builtin_fns["isequal"] = BuiltinFn("isequal",builtinfnarglist_206)
builtin_fns["isequal"].signatures.append(builtinfnarglist_207)
builtin_fns["isequal"].signatures.append(builtinfnarglist_208)
builtin_fns["isequal"].signatures.append(builtinfnarglist_209)
builtin_fns["isnotequal"] = BuiltinFn("isnotequal",builtinfnarglist_210)
builtin_fns["isnotequal"].signatures.append(builtinfnarglist_206)
builtin_fns["isnotequal"].signatures.append(builtinfnarglist_207)
builtin_fns["isnotequal"].signatures.append(builtinfnarglist_208)
builtin_fns["isnotequal"].signatures.append(builtinfnarglist_209)
builtin_fns["isnotequal"].signatures.append(builtinfnarglist_210)
builtin_fns["isgreaterequal"] = BuiltinFn("isgreaterequal",builtinfnarglist_206)
builtin_fns["isgreaterequal"].signatures.append(builtinfnarglist_207)
builtin_fns["isgreaterequal"].signatures.append(builtinfnarglist_208)
builtin_fns["isgreaterequal"].signatures.append(builtinfnarglist_209)
builtin_fns["isgreaterequal"].signatures.append(builtinfnarglist_210)
builtin_fns["isless"] = BuiltinFn("isless",builtinfnarglist_206)
builtin_fns["isless"].signatures.append(builtinfnarglist_207)
builtin_fns["isless"].signatures.append(builtinfnarglist_208)
builtin_fns["isless"].signatures.append(builtinfnarglist_209)
builtin_fns["isless"].signatures.append(builtinfnarglist_210)
builtin_fns["islessequal"] = BuiltinFn("islessequal",builtinfnarglist_206)
builtin_fns["islessequal"].signatures.append(builtinfnarglist_207)
builtin_fns["islessequal"].signatures.append(builtinfnarglist_208)
builtin_fns["islessequal"].signatures.append(builtinfnarglist_209)
builtin_fns["islessequal"].signatures.append(builtinfnarglist_210)
builtin_fns["isfinite"] = BuiltinFn("isfinite",builtinfnarglist_186)
builtin_fns["isfinite"].signatures.append(builtinfnarglist_187)
builtin_fns["isfinite"].signatures.append(builtinfnarglist_189)
builtin_fns["isfinite"].signatures.append(builtinfnarglist_234)
builtin_fns["isfinite"].signatures.append(builtinfnarglist_235)
builtin_fns["isinf"] = BuiltinFn("isinf",builtinfnarglist_186)
builtin_fns["isinf"].signatures.append(builtinfnarglist_187)
builtin_fns["isinf"].signatures.append(builtinfnarglist_189)
builtin_fns["isinf"].signatures.append(builtinfnarglist_234)
builtin_fns["isinf"].signatures.append(builtinfnarglist_235)
builtin_fns["isnan"] = BuiltinFn("isnan",builtinfnarglist_186)
builtin_fns["isnan"].signatures.append(builtinfnarglist_187)
builtin_fns["isnan"].signatures.append(builtinfnarglist_189)
builtin_fns["isnan"].signatures.append(builtinfnarglist_234)
builtin_fns["isnan"].signatures.append(builtinfnarglist_235)
builtin_fns["isnormal"] = BuiltinFn("isnormal",builtinfnarglist_186)
builtin_fns["isnormal"].signatures.append(builtinfnarglist_187)
builtin_fns["isnormal"].signatures.append(builtinfnarglist_189)
builtin_fns["isnormal"].signatures.append(builtinfnarglist_234)
builtin_fns["isnormal"].signatures.append(builtinfnarglist_235)
builtin_fns["isordered"] = BuiltinFn("isordered",builtinfnarglist_178)
builtin_fns["isordered"].signatures.append(builtinfnarglist_183)
builtin_fns["isordered"].signatures.append(builtinfnarglist_176)
builtin_fns["isordered"].signatures.append(builtinfnarglist_254)
builtin_fns["isordered"].signatures.append(builtinfnarglist_255)
builtin_fns["signbit"] = BuiltinFn("signbit",builtinfnarglist_38)
builtin_fns["signbit"].signatures.append(builtinfnarglist_257)
builtin_fns["signbit"].signatures.append(builtinfnarglist_258)
builtin_fns["signbit"].signatures.append(builtinfnarglist_259)
builtin_fns["signbit"].signatures.append(builtinfnarglist_260)
builtin_fns["any"] = BuiltinFn("any",builtinfnarglist_8)
builtin_fns["all"] = BuiltinFn("all",builtinfnarglist_8)
builtin_fns["bitselect"] = BuiltinFn("bitselect",builtinfnarglist_31)
builtin_fns["select"] = BuiltinFn("select",builtinfnarglist_31)
builtin_fns["vload2"] = BuiltinFn("vload2",builtinfnarglist_265)
builtin_fns["vload3"] = BuiltinFn("vload3",builtinfnarglist_266)
builtin_fns["vload8"] = BuiltinFn("vload8",builtinfnarglist_267)
builtin_fns["vload16"] = BuiltinFn("vload16",builtinfnarglist_268)
builtin_fns["vstore2"] = BuiltinFn("vstore2",builtinfnarglist_269)
builtin_fns["vstore4"] = BuiltinFn("vstore4",builtinfnarglist_270)
builtin_fns["vstore8"] = BuiltinFn("vstore8",builtinfnarglist_271)
builtin_fns["vstore16"] = BuiltinFn("vstore16",builtinfnarglist_272)
builtin_fns["vload_half"] = BuiltinFn("vload_half",builtinfnarglist_273)
builtin_fns["vload_half2"] = BuiltinFn("vload_half2",builtinfnarglist_273)
builtin_fns["vload_half4"] = BuiltinFn("vload_half4",builtinfnarglist_273)
builtin_fns["vload_half8"] = BuiltinFn("vload_half8",builtinfnarglist_273)
builtin_fns["vload_half16"] = BuiltinFn("vload_half16",builtinfnarglist_273)
### END GENERATED CODE ###

### END GENERATED CODE ###

################################################################################
#                      TYPING CHECKING RULES                                   #
################################################################################
class TypeDefinitions(object):
    """ This class contains a definition of C99.
    
    We build up types and then call `exists` just before introducing
    a variable in to the context in order to ensure that the variable's type is
    valid C99.
    """ 
    def __init__(self, context):       
        """Populates the object with data from the c99 specification."""
        #The context
        self._g = context
        
        #built-in OpenCL Integer
        ### Begin generated code ###
        self.functions = builtin_fns
    
        ### End generated code ###
        
        #Valid substitutions
        self.valid_substituations = [(Type(x[0]),Type(x[1])) for x in c99_substitutions]
        
        #built-in type names
        self.types = list()
        for scalar_t in c99_scalar_types: self.types.append(scalar_t)
        for vector_t in c99_vector_types: self.types.append(vector_t)

        #Built-in type casting functions
        #qualifiers [C99 6.7.3]
        self.quals = list()
        self.quals.append("const")
        self.quals.append("restrict")
        self.quals.append("volatile")
        #,,,
        
        #storage specifiers
        self.storage = list()
        self.storage.append("typedef")
        #...
        
        #function specifiers, enforced during parsing.
        self.funcspec = ("inline","explicit","virtual")
    
    def typename_exists(self, name):
        if name in self.types:
            return True
        for n in self._g.typenames:
            if name == n.name: return True
        return False
           
    def is_valid_name(self, name):
        if name == None:
            return True
        
        if name[0] == "!":
            return False
        return True
    
    def cond_type(self):
        """ Expected type of a conditional. 
        
        To check if conditional term with type cond_type is correct, use 
        if not self._g.type_defs.sub(cond_type,self._g.type_defs.cond_type()):
            raise...
        """
        return Type("bool")
    
    def dim_type(self):
        """Expected type of a dimension for an array.
        
        To check if a dimension is correct use this functio nas one of the args
        to `equal`.
        """
        return Type("size_t")
    
    def switch_type(self):
        """Type of switch statements."""
        return None #The full switch statement shouldn't resolve to a type.
    
    def subs_type(self):
        """Subscript type."""
        return self.dim_type()
    
    def exists(self, type):
        """Raises an exception only if the type is invalid according to c99
        
        Each type is responsible  for implementing exists in terms of 
        the current definition.
        """
        type.exists(self)
    
    def return_type(self, op, lhs, rhs=None):
        """Typechecks and resolves types for ops, including assignment."""
        if(op in c99_conditional_ops):
            if rhs == None or not self.sub(lhs,rhs):
                raise TargetTypeCheckException("lhs and rhs of = should have "+
                                               "compatable types:"+
                                               "(%s,%s)" % (str(lhs),str(rhs)),
                                               None)
            return self.cond_type() 
        
        if op in c99_unary_ops:
            #TODO
            return lhs
        
        if(op in c99_binops):
            if rhs == None:
                raise TargetTypeCheckException(
                            "%s is a binop but only one argument is defined."%
                            op,None)
            try:
                return Type(c99_op_pairs[lhs.name,rhs.name])
            except KeyError as e:
                raise TargetTypeCheckException("Operation between %s and %s "%
                                               (lhs.name,rhs.name)+"undefined.",
                                               None)
        if(op == "="):
            if rhs == None:
                raise TargetTypeCheckException("= is binary but only one " +
                                               "argument given",None)
            if not self.sub(lhs,rhs):
                raise TargetTypeCheckException("lhs and rhs of = should have "+
                                               "compatable types:"+
                                               "(%s,%s)" % (str(lhs),str(rhs)),
                                               None)
            return lhs
    
    def matching_quals(self, lhs,rhs):
        return True
    
    def _sub_str_char(self,lhs,rhs):
        """ char* = string b/c that's what the parser does. """
        if lhs.name == "string" and rhs.name == "char":
            return rhs.is_ptr or rhs.is_array
        if rhs.name == "string" and lhs.name == "char":
            return lhs.is_ptr or lhs.is_array

    def _is_ptr(self, t):
        return t.is_array or t.is_ptr

    def sub(self, lhs, rhs):
        """Returns true if lhs and be used where rhs is expected."""
        if not isinstance(lhs, Type): 
            raise TargetTypeCheckException("Expected Type instance but got %s" %
                                           lhs.__class__, None)
        if not isinstance(rhs, Type): 
            raise TargetTypeCheckException("Expected Type instance but got %s" %
                                           rhs.__class__, None)
        
        #char* and string considered the same here.
        if self._sub_str_char(lhs,rhs):
            return True
        
        if self._is_ptr(lhs) and self._is_ptr(rhs):
            return True
        elif self._is_ptr(lhs):
            if transitive_sub("int", rhs.name):
                return True
            raise TargetTypeCheckException("Cannot operate on a ptr and" + 
                                           "a non-ptr.", None)
        elif self._is_ptr(rhs):
            if transitive_sub("int", lhs.name):
                return True
            raise TargetTypeCheckException("Cannot operate on a ptr and" + 
                                           "a non-ptr.", None)
        
        if lhs.name == rhs.name or transitive_sub(lhs.name, rhs.name):
            return True
        
        return False
        
    def reserved(self):
        """Words that cannot be used as variable names."""
        reserved_words = list()
        for w in self.types: reserved_words.append(w)
        for w in self.quals: reserved_words.append(w)
        for w in self.funcspec: reserved_words.append(w)
        
        #for w in self._g.typenames: reserved_words.append(w)
        #include names of other types.
        return reserved_words
        

################################################################################
#                                        TYPES                                 #
################################################################################

class Type(object):
    """Represents a declared type. 
    
    This class Contains all the information used by TypeDefinitions, contains
    type-specific logic for entering and leaving a scope, and is 
    subclassed for structs and functions. 
    """
    def __init__(self, name):
        """Initialized a new Type; based on Decl in pycparser/c_ast.cfg
        
        name: declaration type
        quals: list of qualifiers (const, volatile)
        funcspec: list function specifiers (i.e. inline in C99)
        storage: list of storage specifiers (extern, register, etc.)
        bitsize: bit field size, or None
        """
        
        self.name           = name
        self.declared_name  = None
        self.quals          = list()
        self.storage        = list()
        self.funcspec       = list()
        self.bitsize        = None 
        
        #Array types
        self.is_array = False
        self.dim      = None
        
        #Ptr types
        self.is_ptr   = False

    def enter_scope(self, v, g, scope):
        """Adds `Variable` v to `Scope` scope in `Context` g
        
        Types handle scopes in a dispatch-style pattern because the result of 
        adding a `Variable` to the scope varies depending upon the type of the
        `Variable`. For example, enum types add some ints to the context, and
        structs/typedefs add new type keywords."""
        #ensure that this type exists.
        g.type_defs.exists(self)
        
        # Create the variable identifier if it doesn't already exist.
        if not g._variables.has_key(v.name):
            g._variables[v.name] = v
        
        #Add the variable to the scope.
        v.add_scope(scope, self)
    
    def leave_scope(self, v, g, scope):
        """Removes `Variable` v from `Scope` scope in `Context` g
        
        See enter_scope."""
        v.remove_scope(scope)

    def add_qual(self, qual):
        self.quals.append(qual)
    
    def add_storage_spec(self, s):
        self.storage.append(s)
    
    def set_bitsize(self, bitsize):
        self.bitsize = bitsize
    
    def exists(self, type_defs):
        """See `TypeDefinitions.exist`"""
        if not type_defs.typename_exists(self.name):
            raise TargetTypeCheckException("Typename %s unknown"%self.name,None)
        for q in self.quals:
            if not q in type_defs.quals:
                raise TargetTypeCheckException("Qualifier %s unknown"%q,None)
        for fs in self.funcspec:
            if not fs in type_defs.funcspec:
                raise TargetTypeCheckException(
                                   "Function Specifier %s unknown"%fs,None)
        for s in self.storage:
            if not s in type_defs.storage:
                raise TargetTypeCheckException(
                                    "Storage specifier %s unknown"%s,None)
        return True

    def __str__(self):
        name = "Type: %s " % self.name
        for q in self.quals:
            name = name + "%s " % q
        for s in self.storage:
            name = name + "%s " % s
        for f in self.funcspec:
            name = name + "%s " % f
        if self.is_array:
            name = name + "[%s] " % self.dim
        if self.is_ptr:
            name = name + "*"
        return name
            
class StructType(Type):
    """A struct that handles type names."""
    def __init__(self, name, members):
        super(StructType, self).__init__(name)
        self.name    = name
        self.members = members
    
    def has_member(self, name):
        for n in self.members.keys():
            if n == name: return True
        return False
    
    def get_type(self, name):
        for n in self.members.keys():
            if n == name: return self.members[n]
        raise TargetTypeCheckException("Struct member %s not found"%name,None)

    def exists(self, type_defs):
        #Ensure that all member types are valid types.
        for m_type in self.members.values():
            m_type.exists(type_defs)

class TypeDef(Type):
    """A typedef."""
    def __init__(self, tagname, type):
        """Constructor.
        
        typename = the name of this type
        type     = `Type` represented by typename
        """
        super(TypeDef, self).__init__(tagname)
        self.tagname = tagname
        self.type     = type
    
    def enter_scope(self, v, g, scope):
        g.add_typename(self.tagname,scope)
        super(TypeDef,self).enter_scope(v,g,scope)

    def leave_scope(self, v, g, scope):
        pass
        
class EnumType(Type):
    """An enum type."""
    def __init__(self, name, values):
        """Constructor.
        
        name = name of variable (optional). The Type.name name of all enums is
        simply enum. the `name` passed into this constructor is only used to 
        create a variable with the correct name.
        """
        super(EnumType,self).__init__("enum")
        self._enum_name = name
        self.enum_values = values if not values == None else list()
    
    @classmethod
    def enum_value_type(cls):
        t = Type("int")
        t.add_qual("const")
        return t

    @classmethod
    def enum_name_type(cls):
        return Type("int")
        
    def enter_scope(self, v, g, scope):
        """All enum values + the name should go in and out of scope together."""
        g.type_defs.exists(self)
        
        #If a name was given to the enum, add a new typename to the scope.
        if not self._enum_name == None:
            g.add_variable(self._enum_name, self.enum_name_type(), scope)
        
        #Add each of the enum values to the context.
        for value_name in self.enum_values:            
            g.add_variable(value_name, self.enum_value_type(), scope)
    
    def leave_scope(self,v,g,scope):
        v.remove_scope(scope)
        
    def exists(self, type_defs):
        for v in self.enum_values:
            if v in type_defs.reserved():
                raise TargetTypeCheckException("Reserved word in enum def",None)
        if self._enum_name in type_defs.reserved():
            raise TargetTypeCheckException("enum name with reserved word",None)
        return True

class EllipsisType(Type):
    """A dummy type for ellipses in function arguments."""
    def __init__(self):
        super(EllipsisType,self).__init__("...")
    
    def enter_scope(self,v,g,scope):
        pass
    def leave_scope(self,v,g,scope):
        pass
    def exists(self,type_defs):
        return True

class FunctionType(Type):
    """A function type. """
    
    def __init__(self, name, param_types=list(), return_type=Type("void")):
        super(FunctionType,self).__init__(name)
        self.name          = name # Part of the type because functions aren't values.
        self.param_types   = param_types
        self.return_type    = return_type 

    def has_ellipsis(self):
        for t in self.param_types:
            if isinstance(t, EllipsisType):
                return True
        return False
    
    def enter_scope(self, v, g, scope):
        g.type_defs.exists(self)
        if not g._variables.has_key(v.name):
            g._variables[v.name] = v
        v.add_scope(scope, self)
        g.functions.append(v)
    
    def exists(self, type_defs):
        for pt in self.param_types:
            if isinstance(pt, FunctionType):
                raise TargetTypeCheckException("HOFs not support by C")
            type_defs.exists(pt)
        if isinstance(self.return_type, FunctionType):
            raise TargetTypeCheckException("HOFs not support by C")
        type_defs.exists(self.return_type)
        return True


################################################################################
#                                 IDENTIFIERS                                  #
################################################################################

#This class should be renamed to Identifier.
class Variable(object):
    """A Variable is an Identifier and a scope. 
    
    Variable has multiple Types because the same identifier can have different
    types depending on the scope.
    """
    def __init__(self, name):
        self.name = name
        self.scope = list() #stack
        self.type  = dict() #{scope : type, ...}


    def _return_type(self, type):
        if isinstance(type, TypeDef):
            return type.type
        else:
            return type

    def get_type(self):
        return self._return_type(self.type.get(self.scope[-1]))
    
    def get_type_at_scope(self, scope):
        return self._return_type( self.type[scope] )

    def add_scope(self, scope, type):
        if not isinstance(type, Type):
            raise TargetTypeCheckException(
             "Expecting an instance of Type but got %s" % type.__class__,None)    
        self.scope.append(scope)
        self.type[scope] = type
        
    def remove_scope(self, scope):
        if self.scope.count(scope) > 0:
            self.scope.remove(scope)
        if self.type.has_key(scope):
            self.type.pop(scope)

    def __str__(self):
        return "%s : %s" % (self.name, str(self.type))

################################################################################
#                                 CONEXT                                       #
################################################################################

class TypeName(object):
    def __init__(self, name):
        self.name = name
        self.scope = list()
        
class Context(object):
    """A context for typechecking C-style programs."""
    def __init__(self):
        self.returning   = False   #True iff checker is inside a return stmt
        self.functions   = list()  #stack, determines type of returning func.
        self._variables  = dict()  #name -> `Variable`  
        self._scope      = list()  #stack.
        self._scope.append(0)
        self.unresolved_forward_decls = list() #of variable names.
        
        self.typenames = list()  #of TypeNames
        
        #Type definitions for the context.
        self.type_defs = TypeDefinitions(self)
        
        #Context variables specific to a statement's form.
        self.switch_type = Type(None) #Type of switch condition.
    
        #Other misc. context
        self.in_decl = False #in declaration

    def get_function(self, name):
        """Returns the Type of a function."""
        if name in self._variables:
            return self.get_variable(name).get_type()
        
        elif name in self.type_defs.functions:
            return self.type_defs.functions[name]
        else:
            raise TargetTypeCheckException(
                                    "No function named %s in current context"%
                                    name, None)
            
    def is_typename(self,name):
        for t in self.typenames:
            if t.name == name: return True
        return False
    
    def get_typename_type(self, name):
        return self.get_variable(name).get_type()
            
    def add_typename(self, name, scope):
        #TODO check to make sure it's not a reserved name
        for t in self.typenames:
            if t.name == name:
                t.scope.append(scope)
                return True
        t = TypeName(name)
        t.scope.append(scope)
        self.typenames.append(t)
        return True
    
    def get_variable(self, variable_name):
        """Returns a `Variable` with variable_name as its identifier."""
        if self._variables.has_key(variable_name):
            return self._variables.get(variable_name)
        else:
            raise TargetTypeCheckException("Variable %s not in scope "
                                           % variable_name, None)     
    
    def add_variable(self, variable_name, type, node=None):
        """Adds a variable to the current scope."""
        if not self.type_defs.is_valid_name(variable_name):
            raise TargetTypeCheckException(
                            "Invalid identifier or type name: %s"%
                            variable_name, node)
        
        if not isinstance(type, Type):
            raise TargetTypeCheckException(
                                        "Expected subclass of Type but got %s"%
                                        str(type), node)
        
        if variable_name in self.type_defs.reserved():
            raise TargetTypeCheckExpceiton("%s is a reserved word."%
                                           variable_name,Node)
        
        scope = self._scope[-1] #the current scope.

        # Ensure that this variable isn't already defined for the current scope.
        if self._variables.has_key(variable_name) and \
           self._variables[variable_name].scope.count(scope) > 0:
            raise TargetTypeCheckException("Cannot redeclare %s"%variable_name + 
                " (%s) as a different symbol (%s)" % 
                (self._variables[variable_name].get_type_at_scope(scope), type),
                node)
        
        # Add the identifier to the scope.
        if variable_name in self._variables.keys():
            type.enter_scope(self._variables.get(variable_name), self,scope)
        else:    
            type.enter_scope(Variable(variable_name),self,scope)
    
    def remove_variable(self, variable):
        if self._variables.has_key(variable.name): 
            self._variables.pop(variable.name) #TODO
        

    def change_scope(self):
        """Adds another scope. Everything previously in scope remains so."""
        self._scope.append(0 if len(self._scope) == 0  else self._scope[-1] + 1)
        
        
    def leave_scope(self):
        """Moves down in scope. Removes all variables that go out of scope."""
        
        scope = self._scope[-1] #the current scope.
        
        #Remove scope from all identifiers and type names.
        for v in self._variables.values():
            if scope in v.scope:
                v.get_type_at_scope(scope).leave_scope(v,self,scope)
        for t in self.typenames:
            if scope in t.scope:
                t.scope.remove(scope)
        #Remove out-of-scope identifiers from identifier list.
        vars_to_rm = []
        for v in self._variables.values():
            if len(v.scope) == 0: vars_to_rm.append(v.name)
        for name in vars_to_rm: self._variables.pop(name)
        #Removed out-of-scope typenames
        typenames_to_rm = []
        for t in self.typenames:
            if len(t.scope) == 0: typenames_to_rm.append(t)
        for t in typenames_to_rm: self.typenames.remove(t) 
        #Remove out-of-scope functions.
        funcs_to_rm = []
        for f in self.functions:
            if len(f.scope) == 0: funcs_to_rm.append(f)
        for f in funcs_to_rm: self.functions.remove(f)
        
        self._scope.pop()
                


################################################################################
#                            UTILITY CLASSES                                   #
################################################################################
class TargetTypeCheckException(Exception):
    """Raised to indicate an error during type checking of generated code."""
    def __init__(self, message, node):
        self.message = message
        self.node = node


################################################################################
#                                TYPESCHECKING                                 #
################################################################################
        
class OpenCLTypeChecker(pycparser.c_ast.NodeVisitor):
    """Type checks OpenCL code.
    
    To use:
        tc = OpenCLTypeChecker(context)
        tc.visit(node) 
    """
    
    def __init__(self, context=Context()):
        """context = a pycparserext.typechecker.Context object."""
        self._g = Context()                                                     #To get auto-complete in my IDE... TODO remove.
        self._g = context
        
    def generic_visit(self, node):
        """Raises an error when no visit_XXX method is defined."""
        raise TargetTypeCheckException("visit_%s undefined" % 
                                       node.__class__.__name__, node)
    def visit_children(self, node):
        for c_name, c in node.children():
            self.visit(c)

    def visit_FileAST(self, node):
        self.visit_children(node)
    
    def visit_Default(self, node):
        self.visit_children(node)
    
    def visit_DoWhile(self, node):
        """Ensures conditional has correct type and statement is well-typed."""
        cond_type = self.visit(node.cond)
        if not self._g.type_defs.sub(cond_type,self._g.type_defs.cond_type()):
            raise TargetTypeCheckException(
                        "Expected condition (%s or similar) but found %s" %
                        (str(self._g.type_defs.cond_type()), str(cond_type)), 
                        node)
        self._g.change_scope()
        self.visit(node.stmt)
        self._g.leave_scope()
        
    def visit_While(self, node):
        return self.visit_DoWhile(node)

    def visit_For(self, node):
        self._g.change_scope()
                
        self.visit(node.init)
                
        cond_type = self.visit(node.cond)
        if not self._g.type_defs.sub(cond_type,self._g.type_defs.cond_type()):
            raise TargetTypeCheckException(
                        "Expected condition of for to be %s but found %s" %
                        (str(self._g.type_defs.cond_type()), 
                         str(cond_type)), node)
        
        self.visit(node.next)
        self.visit(node.stmt)
        
        self._g.leave_scope()
        
    
    def visit_Goto(self, node):
        self.visit_children(node)
    def visit_Label(self, node):
        self.visit_children(node)

    def visit_TernaryOp(self, node):
        """Ensures conditional is correct type and expression types match."""
        cond_type = self.visit(node.cond)
        if not self._g.type_defs.sub(cond_type,self._g.type_defs.cond_type()):
            raise TargetTypeCheckException(
                        "Expected condition of ternary to be %s but found %s" %
                        (str(self._g.type_defs.cond_type()), str(cond_type)), 
                        node)
        
        t_t = self.visit(node.iftrue)
        f_t = self.visit(node.iffalse)
        if not self._g.type_defs.sub(t_t,f_t):
            raise TargetTypeCheckException(
                        "Ternary condition expressions %s and %s don't match" %
                        (str(t_t), str(f_t)), node)
        
        return t_t
    
    def visit_Switch(self, node):
        """Adds switch to the context."""
        cond_type = self.visit(node.cond)
        self._g.switch_type = cond_type
#        if not cond_type == Type.get_cond_type():
#            raise TargetTypeCheckException(
#                "Expected conditional type for switch condition but found %s"% 
#                cond_type, node)
        
        #Visit each case statement.
        self.visit(node.stmt)
        self._g.switch_type = Type(None)
    
    def visit_Case(self, node):
        label_t = self.visit(node.expr)
        if not self._g.type_defs.sub(label_t,self._g.type_defs.switch_type()):
            raise TargetTypeCheckException(
                                "Case label of type %s does not reduce to %s"%
                                (str(label_t), str(self._g.switch_type)), node) 
        for s in node.stmts:
            self.visit(s)
    
    def visit_If(self, node):
        cond_type = self.visit(node.cond)
        if not self._g.type_defs.sub(cond_type,self._g.type_defs.cond_type()):
            raise TargetTypeCheckException(
                "Expected conditional type for if condition but found %s"% 
                cond_type, node)
        
        self._g.change_scope()
        self.visit(node.iftrue)
        self._g.leave_scope()
        self._g.change_scope()
        if not node.iffalse == None: #else portion is optional.
            self.visit(node.iffalse)
        self._g.leave_scope()

    def visit_FuncDeclExt(self, node):
        """Extended (OpenCL) function definition."""
        # Get the function's name and return type
        #function_name = node.type.declname
        return_type = self.visit(node.type)
        function_name = return_type.declared_name
        
        # Get the function parameter names and types
        param_names = list()
        param_types = list()
        if not node.args == None:
            for param in node.args.params:
                t = self.visit(param)
                param_names.append(t.declared_name)
                param_types.append(t)

        # Create the function type
        func_t = FunctionType(function_name, param_types, return_type)
        
        # Add the function declaration
        if not self._g._variables.has_key(func_t.name):    
            self._g.add_variable(func_t.name, func_t, node)
            self._g.unresolved_forward_decls.append(function_name)
        else:
            f = self._g.get_variable(function_name).get_type()
            
            if not self._g.type_defs.sub(f.return_type, func_t.return_type):
                raise TargetTypeCheckException("Reclaraction of fwd decl",node)
            for (t1,t2) in zip(f.param_types,func_t.param_types):
                if not self._g.type_defs.sub(t2, t1):
                    raise TargetTypeCheckException("Reclaraction of fwd decl",
                                                   node)
            return f
        
        return func_t
                

    def visit_FuncDef(self, node):
        """Function body definition."""
        # Create a new scope for the function defintion.
        self._g.change_scope()
        
        function_t = self.visit(node.decl)
        
#        param_names = [t.declared_name for t in function_t.param_types]
#        param_types = [t for t in function_t.param_types]
        
#        # Add params to the function's scope
#        for n,t in zip(param_names,param_types):
#            self._g.add_variable(n, t, node)
            
        # Visit expressions in the body of the function.
        self.visit(node.body)
        
        # Move out of the function definition scope.
        self._g.leave_scope()
        
        #Add the function to the surrounding scope.
        if not self._g._variables.has_key(function_t.name):
            self._g.add_variable(function_t.name, function_t, node)
        
        return function_t
    
    def visit_Compound(self, node): 
        self.visit_children(node)
    
    def visit_Return(self, node):
        """Ensures the returned value's type is the function's return_type."""
        self._g.returning = True
        return_type = self.visit(node.expr)
        self._g.returning = False
        
        f = self._g.functions[-1].get_type()
        if not self._g.type_defs.sub(return_type,f.return_type):
            raise TargetTypeCheckException(
                        "returning from %s expected %s but got %s" %
                        (str(f), str(f.return_type), str(return_type)), node)
    
    def visit_FuncCall(self, node):
        func_type = self._g.get_function(node.name.name)

        #Get the parameter types
        param_types = list()
        if not node.args == None:
            for e in node.args.exprs:
                param_types.append(self.visit(e))
        
        #Handle builtin functions
        if isinstance(func_type, BuiltinFn):
            if not func_type.check(param_types):
                raise TargetTypeCheckException(
                        "Polymorphic builtin %s does not take %s" %
                        (str(func_type), 
                         "{" + ",".join([str(t) for t in param_types]) + "}"
                         ),node)
            return func_type.return_type(param_types)

        #Ensure that the variable is a FunctionType if it's not a builtin fn
        if not isinstance(func_type,FunctionType):
            raise TargetTypeCheckException("called '%s' is not a function"%
                            func_type.name, node)
        
        # Ensure that the parameter lists are the same size
        if not len(param_types) == len(func_type.param_types) \
        and (not func_type.has_ellipsis() \
             or len(param_types) < len(func_type.param_types)-1):        
            raise TargetTypeCheckException(
                    "%s arguments passed to %s, but expected %s" %
                    (len(param_types), 
                     str(func_type), 
                     len(func_type.param_types)
                    ), node)
        
        # Ensure that parameter types match declared parameter types.
        #Because zip uses  the smallest list, 
        for p,ep in zip(param_types, func_type.param_types):
            #Skip over the ellipsis, anything following won't be in the zip.
            if isinstance(ep, EllipsisType):
                continue
            if not self._g.type_defs.sub(p, ep):
                raise TargetTypeCheckException(
                        "Arguments to %s are incorrect: expected %s but got %s"%
                        (str(func_type),ep,p), node)
        
        # Return the return type.
        return func_type.return_type 
    
    def visit_Continue(self, node):
        pass
    
    def visit_Decl(self, node):
        """Adds a declared variable to the context."""
        self._g.in_decl = True
        
        name = node.name
        
        #Get the base type
        type = self.visit(node.type)
        
        #Skip functions; handled by visit_funcdeclext.
        if isinstance(type, FunctionType):
            return type
        
        if not isinstance(type,Type):
            raise TargetTypeCheckException("Expected Type but found %s" %
                                           str(type),node)
        
        #Enrich the type with c99 goodness.
        if not node.quals == None:
            for q in node.quals:
                type.add_qual(q)
        if not node.storage == None:
            for s in node.storage:
                type.add_storage_spec(s)
        if not node.bitsize == None:
            type.set_bitsize(node.bitsize)
        
        self._g.add_variable(name, type, node)
        
        
        #Ensure that type of the initial value matches the declared type.
        if node.init != None:
            #could be a list or a scalar.
            initial_value_type = self.visit(node.init)
             
            if isinstance(initial_value_type,types.ListType):
                if (type.is_array or type.is_ptr):
                    if not len(initial_value_type) == type.dim:
                        raise TargetTypeCheckException(
                        "Type %s expected dimension %s but initialized to %s"%
                            (str(type),str(type.dim),len(initial_value_type)),
                            node)
                    
                    was_array = type.is_array 
                    type.is_array = False
                    was_ptr = type.is_ptr
                    type.is_ptr   = False
                    for t in initial_value_type:
                        if not self._g.type_defs.sub(t, type):
                            raise TargetTypeCheckException(
                            "Wrong type in initializer for %s"%str(type),node)
                    type.is_array = was_array
                    type.is_ptr = was_ptr
                    
                elif isinstance(type, StructType):
                    if len(initial_value_type) > len(type.members.values()):
                        raise TargetTypeCheckException(
                        "Type %s expected dimension %s but initialized to %s"%
                            (str(type),len(type.members.values()),
                             len(initial_value_type)),node)
                    for t,et in zip(initial_value_type, type.members.values()):
                        if not self._g.type_defs.sub(t,et):
                            raise TargetTypeCheckException(
                            "Expected %s but found %s in struct initializer %s"%
                            (str(et),str(t),str(type)), node)
                            
                else:
                    raise TargetTypeCheckException("Assigned list to scalar %s"%
                                                   str(type),node)         
           
            else:
                if not self._g.type_defs.sub(initial_value_type,type):
                    raise TargetTypeCheckException(
                    "Incompatable types when assigning to %s (%s) from type (%s)"% 
                    (name, str(type), str(initial_value_type)), node)

        self._g.in_decl = False
        return type
        
        

    def visit_DeclList(self, node):
        self.visit_children(node)
    
    def visit_Assignment(self, node):
        """Defers to C99 spec as defined in Type"""
        #Typechecking logic handled by the types.
        lvalue = self.visit(node.lvalue)
        if "const" in lvalue.quals and not self._g.in_decl:
            raise TargetTypeCheckException("Assignment of read-only %s" %
                                           str(lvalue), node)
            
        return self._g.type_defs.return_type(node.op, 
                                             self.visit(node.lvalue), 
                                             self.visit(node.rvalue))
    
    def visit_UnaryOp(self, node):
        """Defers to C99 spec as defined in Type"""
        return self._g.type_defs.return_type(node.op,self.visit(node.expr))
    
    def visit_BinaryOp(self, node):
        """Defers to C99 spec as defined in Type"""
        return self._g.type_defs.return_type(node.op,self.visit(node.left),
                                          self.visit(node.right))
    
    def visit_Break(self):
        pass
        
    def visit_ID(self, node):
        """Gets the type of an already declared variable.
        
        Context does exception handling if ID isn't defined.
        """
        return self._g.get_variable(node.name).get_type()
    
    def get_ID(self, node):
        """Returns the name of the ID instead of its type."""
        return node.name
    
    def visit_Cast(self, node):
        """Returns the type to which the variable is casted."""
        return self.visit(node.to_type)
    
    def visit_Typename(self, node):
        t = self.visit(node.type)
        for q in node.quals:
            t.add_qual(q)
        return t
            
    
    
    def visit_Constant(self, node):
        return Type(node.type)
    
    def visit_TypeDecl(self, node):
        #                                                                        TODO quals?
        """Returns the Type for the base type.
        
        I believe that visit_Decl is the only way that visit_TypeDecl is ever
        called. If this is not true, then this function should add the variable
        to the context.
        """
        t = self.visit(node.type)
        t.declared_name = node.declname
        
        if not node.quals == None:
            for q in node.quals:
                t.add_qual(q)
    
        return t

    def visit_IdentifierType(self, node):
        """Returns a Type for the identifier.""" #TODO Quals?
        name = " ".join(node.names)
        
        if self._g.is_typename(name):
            return self._g.get_typename_type(name)
        else:
            return Type(name)

    def visit_PreprocessorLine(self, node):
        if re.match("^\#include\ ", node.contents):
            file_name = (node.contents.split(" ")[-1])
            file_name = file_name.replace("\n","")
            file_name = file_name.replace("\"","")
            file_name = file_name.replace("<","")
            file_name = file_name.replace(">","")
            header_code = ""
            
            #Get header_code
            paths = ""
            try:
                paths = os.environ['ACE_OCL_INCLUDES']
            except Exception as e:
                raise TargetTypeCheckException("Must define envvar "+
                                               "ACE_OCL_INCLUDES",node)
            include_paths = paths.split(";")
            for path in include_paths:
                f = None
                try:
                    f = open(path + "/" + file_name)
                except Excpetion as e:
                    continue
                for line in f:
                    header_code = "%s%s" % (header_code,line)
                #Typecheck the header file, adding to this context.
                tc = TypeChecker()
                tc_Context = self._g
                tc.check(header_code, tc_Context)
                break     
            if header_code == "":
                raise TargetTypeCheckException(
                                            "Could not find file \"%s\" in %s" %
                                            (file_name, paths), node)
        else:
            raise TargetTypeCheckException("Expected preprocessed code "+
                                       "but found a preprocessor line.", node)

       
    
    def visit_ArrayDecl(self, node):
        t = self.visit(node.type)
        dim_t = self.visit(node.dim)
        
        if not self._g.type_defs.sub(dim_t, self._g.type_defs.dim_type()):
            raise TargetTypeCheckException(
                            "Expected valid Array dimension type but found %s"%
                            str(dim_t), node)
        t.is_array = True
        t.dim = types.IntType(node.dim.value)
        return t
    
    def visit_ExprList(self, node):
        expression_types = list()
        for e in node.exprs: expression_types.append(self.visit(e))
        return expression_types
    
    def visit_ArrayRef(self, node):
        array_t = self.visit(node.name)
        
        if not array_t.is_array:
            raise TargetTypeCheckException(
                    "Attempting subscript access on a non-array type %s" %
                    str(array_t), node) 
        
        subscript_t = self.visit(node.subscript)
        if not self._g.type_defs.sub(subscript_t,self._g.type_defs.subs_type()):
            raise TargetTypeCheckException(
                                        "Arrays cannot be indexed by type %s" % 
                                        str(subscript_t), node)
        
        return array_t
    
    def visit_EmptyStatement(self, node):
        pass
    
    def visit_Enum(self, node):
        enum = EnumType(node.name,list())
        
        for e in node.values.enumerators:
            if not e.value == None:
                v_type = self.visit(e.value)
                if not self._g.type_defs.sub(v_type,EnumType.enum_value_type()):
                    raise TargetTypeCheckException(
                            "Expected enum value to be type %s but found %s" %
                            (EnumType.enum_value_type(), v_type), node)
            enum.enum_values.append(e.name)
        
        return enum
    
    def visit_Enumerator(self, node):
        raise TargetTypeCheckException("Sould be handled by visit_Enum.",node)

    def visit_EnumeratorList(self, node):
        raise TargetTypeCheckException("Sould be handled by visit_Enum.",node)

    def visit_Typedef(self, node):
        t = self.visit(node.type)
        for q in node.quals:
            t.add_qual(q)
        for s in node.storage:
            t.add_storage_spec(s)
        
        type = TypeDef(node.name, t)
        self._g.add_variable(node.name, type, node)
        return type

    def visit_Struct(self, node):
        #Capture declarations in a new context
        old_g = self._g
        self._g = Context()
        
        if not node.decls == None:
            for m in node.decls:
                self.visit(m)
        
        #Get the types and names of attributes
        members = dict()
        for v in self._g._variables.keys():
            members[v] = self._g.get_variable(v).get_type()
        
        #change to original context and create the struct type
        self._g = old_g
        t = StructType(node.name, members)
        return t
    
    def visit_StructRef(self, node):
        name = self.get_ID(node.name)
        struct = self._g.get_variable(name)
        struct_t = self._g.get_variable(name).get_type() 
        
        if not isinstance(struct_t, StructType):
            raise TargetTypeCheckException("Excpected struct type but found %s"
                                           % str(struct.get_type()), node)
        
        #Ensure correct type argument is used
        if (struct_t.is_ptr and node.type == ".") \
        or (not struct_t.is_ptr and node.type == "->"):
            raise TargetTypeCheckException(
                                    "Invalid type argument of '%s' (have '%s')"%
                                           (node.type,str(struct_t)), node)
        
        return struct.get_type().get_type(self.get_ID(node.field))
        
    def visit_PtrDecl(self, node):
        t = self.visit(node.type)
        t.is_ptr = True
        for q in node.quals:
            t.add_qual(q)
        return t
            
    def visit_Union(self, node):
        """A Union."""
        if self._g.is_typename(node.name):
            return self._g.get_typename_type(node.name)
        else:
            #Treat unions that haven't already been declared like structs.
            if node.decls == None:
                raise TargetTypeCheckException("Storage size of %s unknown"%
                                               node.name,node)
            struct_t =  self.visit_Struct(node)
            type = TypeDef(node.name, struct_t)
            self._g.add_variable(node.name, type, node)
            return struct_t
    
    def visit_EllipsisParam(self, node):
        return EllipsisType()
