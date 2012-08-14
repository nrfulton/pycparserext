from pycparserext.typechecker import Type
from pycparserext.typechecker import GENTYPES
from pycparserext.typechecker import GENTYPES
from pycparserext.typechecker import SGENTYPES
from pycparserext.typechecker import UGENTYPES
from pycparserext.typechecker import IGENTYPES
from pycparserext.typechecker import GENTYPE_SIZES
import cypy

class ParserException(Exception):
    def __init__(self, msg):
        self.message = msg

class Param(object):
    """An argument in a function's signature.
    
    Param parses the information necessary to instantiate a typechecker.Type 
    out of a line in ocl_functions.txt."""
    def __init__(self, p):
        ret = Type("")
        #Handle pointer types
        if p.endswith('*'):
            p=p.replace('*','')
            ret.is_ptr = True
        #Handle qualifiers
        if not p.find(' ') == -1:
            quals = p.split(' ')
            for qual in quals[0:-1]:
                ret.add_qual(qual)
            ret.name = quals[-1]
        else:
            ret.name = p
        self.type = ret
    
    def generate_valid_types(self):
        """Generates a list that associates gentypes with actual OpenCL types.
        
        The OpenCL spec, and particularly \S 6.11.x, is written using gentypes
        as shorthands for sets of types. oct_functions.txt adopts this as well.
        This function associates each of those types with all of the valid
        OpenCL types. Example:
            sgentype -> int,int2,...,char,char2,...,long,long2,... and so on.
        
        The information necessary to update this function can be found be reading
        through the preface to each of the 6.11.x sections in the OpenCL 
        specification.
        
        This class generates instances of pycparserext.typechecker.BuiltinFnArg
        """
        ret     = ""
        types   = list()
        stypes  = SGENTYPES
        ustypes = UGENTYPES
        istypes = IGENTYPES
        sizes   = GENTYPE_SIZES
        
        if self.type.name == "gentype":
            for t in stypes:
                for n in sizes:
                    types.append("\"%s%s\"" % (t,n))
        elif self.type.name == "sgentype":
            for t in stypes:
                types.append("\"%s\"" % t)
        elif self.type.name == "ugentype":
            for t in stypes:
                for n in sizes:
                    types.append("\"%s%s\"" % (t,n))
        elif self.type.name == "igentype":
            for t in istypes:
                for n in sizes:
                    types.append("\"%s%s\"" % (t,n))
        else:
            types.append("\"%s\"" % self.type.name)
        for t in types:
            if not ret == "":
                ret = "%s,%s" % (ret, t)
            else:
                ret = "%s" % t
        return ret

    def var_name(self):
        """The name for this list in generated code."""
        return "builtinfnargtype_%s" % self.num 
        
    def generate_code(self, num):
        """Generates code for this type; the variable name depends on num."""
        self.num = num
        subs = self.generate_valid_types()
        print "%s = BuiltinFnArgType(\"%s\",(%s))" % (self.var_name(),
                                                  self.type.name, subs)
        if self.type.is_ptr:
            print "%s.is_ptr = True" % self.var_name()
        for q in self.type.quals:
            print "%s.add_qual(%s)" % (self.var_name(), q)
Param = cypy.intern(Param)

class ParamList(object):
    """A signature that may be shared by multiple functions."""
    def __init__(self, params, return_type):
        self.params = params
        self.return_type = return_type
    
    def var_name(self):
        return "builtinfnarglist_%s" % self.num

    def generate_type_list(self):
        ret = "("
        for p in self.params:
            ret = "%s%s," % (ret, p.var_name())
        return ret + ")"
    
    def generate_return_type(self):
        return self.return_type.var_name()
            
    
    def generate_code(self, num):
        self.num = num
        types = self.generate_type_list()
        return_type = self.generate_return_type()
        print "%s=BuiltinFnArgList(%s,%s)" % (self.var_name(),
                                              types,
                                              return_type)

class OclFunctionLine(object):
    """Represents a line in ocl_functions.txt. Used for parsing.
    
    The format of each line in ocl_functions.txt is:
    function_name.argtype1,argtype2,...,argtypen.return_type.
    Each of the argtypes and the return type may be either an OpenCL type,
    or an abbreviation for a set of types (see Param.generate_valid_types)
    
    This class generates instances of pycparserext.typechecker.BuiltinFnArgLists
    """
    def __init__(self, line):
        line = line.split(".")
        if len(line) != 3:
            raise ParserException("Expected 3 .'s but found %s:%s"
                                  %(len(line),line))
        self.name = line[0]
        params_line = line[1].split(",")
        while "" in params_line: del params_line[params_line.index("", )]
        self.params = [Param(p) for p in params_line]
        
        line[2] = line[2].replace("\n","")
        self.return_type = Param(line[2])
        
    
    def generate_param_list(self, i, param_lists):
        """Generates all necessary param lists.
        
        We significantly reduce the amount of generated code by sharing param
        lists between functions that have identical signatures. For instance,
        min and max will share a param list.
        """
        found_list = False
        for list in param_lists:
            match = True
            if not len(self.params) == len(list.params):
                match = False
            else:
                for (param, list_item) in zip(self.params, list.params):
                    if  not param == list_item: 
                        match = False
                        break
            if match:
                found_list = True
                self.param_list = list
        if not found_list:
            new_list = ParamList(self.params, self.return_type)
            param_lists.append(new_list)
            self.param_list = new_list
            new_list.generate_code(i)

    def generate_code(self, generated_functions):
        """Generates params, param lists and functions."""
        if not (self.name in generated_functions):
            generated_functions.append(self.name)
            print "builtin_fns[\"%s\"] = BuiltinFn(\"%s\",%s)" % \
                    (self.name,
                     self.name,
                     self.param_list.var_name())
        else:             
            print "builtin_fns[\"%s\"].signatures.append(%s)" % \
                    (self.name, 
                     self.param_list.var_name())

class CodeGenerator(object):
    """Generates code for pychparserext.typechecker.
    
    This class generates a large hashmap that will be used to create builtin
    functions. It uses ocl_functions.txt, which is a transcription of the OpenCL
    specification (\S 6.11.x -- see ocl_functions.txt).
    
    Each line is parsed, and a minimal set of Param and ParamList objects are
    created. Each Param represents an argument or set of valid arguments, and
    each ParamList is a valid signature.
    
    Code is then generated in the following order:
    
    Params: Each unique paramter (gentype, int, float2, etc.) that is
    reified in ocl_functions.txt has only one Param instance.
    
    ParamLists: Similarly, each unique signature has only one parameter
    list objct. A parameter list is very close to a signure, but is distinct 
    because a ParamList can also be a set of signatures. For instance,
        min.gentype.gentype
    is actually a large set of signatures. However, there is only one 
    parameterlist for this function. See the docstring for ParamList for more
    details.
    
    Functions: Each function is a name together with a collection of valid
    signatures. The generated code represents this as a hashmap of lists,
    where the key is the function name and the value is a list of 
    paramter lists. In pycparserext.typechecker, each entry in this 
    hash map is turned into an instance of BuiltinFn.
    """
    def __init__(self):
        self.types = list() #BuiltInFnArgTypes generated by Param
        self.lines = list() #OclFunctionLines
    
    def add_line(self,line):
        self.lines.append(line)
        for p in line.params:
            if not p in self.types:
                self.types.append(p)
        if not line.return_type in self.types:
            self.types.append(line.return_type)
    
    def generate_code(self):

         
        print "### BEGIN GENERATED CODE ###"
        print "#Parameters"
        for i,t in enumerate(self.types):
            t.generate_code(i)
        print ""
        print "#Parameter Lists" 
        param_lists = list() #BuiltinFnParamLists
        for i,l in enumerate(self.lines):
            #generate unqiue paramter lists from lines.
            l.generate_param_list(i, param_lists)
        print ""
        print "#functions"
        print "builtin_fns = dict()"
        generated_functions = list()
        for i,l in enumerate(self.lines):
            l.generate_code(generated_functions)
        print "### END GENERATED CODE ###"
        

#Generate code using ocl_functions.txt.
try:
    cg = CodeGenerator()
    file = open("ocl_functions.txt", "r")
    lines = file.readlines()
    for line in lines:
        if line[0] == "#":
            #print line
            continue
        if line[0] == "\n": continue
        cg.add_line(OclFunctionLine(line))
    cg.generate_code() #generate the code. 
except ParserException as e:
    print "ERROR:" + e.message
    