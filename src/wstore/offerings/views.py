# -*- coding: utf-8 -*-

# Copyright (c) 2013 - 2015 CoNWeT Lab., Universidad Politécnica de Madrid

# This file is part of WStore.

# WStore is free software: you can redistribute it and/or modify
# it under the terms of the European Union Public Licence (EUPL)
# as published by the European Commission, either version 1.1
# of the License, or (at your option) any later version.

# WStore is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# European Union Public Licence for more details.

# You should have received a copy of the European Union Public Licence
# along with WStore.
# If not, see <https://joinup.ec.europa.eu/software/page/eupl/licence-eupl>.

from __future__ import unicode_literals

import json
from urllib2 import HTTPError

from django.http import HttpResponse
from django.contrib.sites.models import get_current_site
from django.core.exceptions import PermissionDenied, ObjectDoesNotExist

from wstore.store_commons.resource import Resource
from wstore.store_commons.utils.http import build_response, get_content_type, supported_request_mime_types, \
authentication_required, identity_manager_required
from wstore.models import Offering, Organization, Resource as OfferingResource
from wstore.models import Context
from wstore.offerings.offerings_management import create_offering, get_offerings, get_offering_info, delete_offering,\
publish_offering, bind_resources, count_offerings, update_offering
from wstore.offerings.resources_management import register_resource, get_provider_resources, delete_resource,\
update_resource, upgrade_resource
from wstore.social.reviews.review_manager import ReviewManager
from wstore.store_commons.errors import ConflictError, RepositoryError
from wstore.social_auth_backend import get_applications


####################################################################################################
#########################################    Offerings   ###########################################
####################################################################################################

def _get_offering(organization, name, version):
    """
    Get the offering object
    Raise: ObjectDoesNotExist in the offering is not found
    """
    # Get the offering
    try:
        org = Organization.objects.get(name=organization)
        offering = Offering.objects.get(name=name, owner_organization=org, version=version)
    except:
        raise ObjectDoesNotExist('Offering not found')

    return offering, org


class OfferingCollection(Resource):

    # Creates a new offering associated with the user
    # that is create a new application model
    @authentication_required
    @supported_request_mime_types(('application/json', 'application/xml'))
    def create(self, request):

        # Obtains the user profile of the user
        user = request.user

        # Get the provider roles in the current organization
        roles = user.userprofile.get_current_roles()

        # Checks the provider role
        if 'provider' in roles:
            try:
                json_data = json.loads(unicode(request.raw_post_data, 'utf-8'))
                create_offering(user, json_data)
            except RepositoryError as e:
                return build_response(request, 502, unicode(e))
            except ConflictError as e:
                return build_response(request, 409, unicode(e))
            except Exception, e:
                return build_response(request, 400, unicode(e))
        else:
            return build_response(request, 403, 'Forbidden')

        return build_response(request, 201, 'Created')

    @authentication_required
    def read(self, request):
        try:
            # Read the query string in order to know the filter and the page
            filter_ = request.GET.get('filter', 'published')
            user = request.user
            action = request.GET.get('action', None)
            sort = request.GET.get('sort', None)

            state = request.GET.get('state', None)
            start = request.GET.get('start', None)
            limit = request.GET.get('limit', None)

            if state:
                if state == 'ALL':
                    state = ['uploaded', 'published', 'deleted']
                else:
                    state = state.split(',')

            # Check sorting values
            if sort is not None:
                if sort != 'date' and sort != 'popularity' and sort != 'name':
                    return build_response(request, 400, 'Invalid query string: Invalid sorting')

            # Build pagination
            pagination = None
            if start and limit:
                pagination = {
                    'skip': start,
                    'limit': limit
                }

            if action != 'count':
                result = get_offerings(user, filter_, state, pagination=pagination, sort=sort)
            else:
                result = count_offerings(user, filter_, state)

        except Exception as e:
            return build_response(request, 400, unicode(e))

        mime_type = 'application/JSON; charset=UTF-8'
        return HttpResponse(json.dumps(result), status=200, mimetype=mime_type)


class OfferingEntry(Resource):

    @authentication_required
    def read(self, request, organization, name, version):
        user = request.user
        try:
            offering, org = _get_offering(organization, name, version)
        except ObjectDoesNotExist as e:
            return build_response(request, 404, unicode(e))
        except Exception as e:
            return build_response(request, 400, unicode(e))

        try:
            result = get_offering_info(offering, user)
        except Exception, e:
            return build_response(request, 400, unicode(e))

        return HttpResponse(json.dumps(result), status=200, mimetype='application/json; charset=UTF-8')

    @authentication_required
    @supported_request_mime_types(('application/json', 'application/xml'))
    def update(self, request, organization, name, version):

        user = request.user
        # Get the offering
        try:
            offering, org = _get_offering(organization, name, version)
        except ObjectDoesNotExist as e:
            return build_response(request, 404, unicode(e))
        except Exception as e:
            return build_response(request, 400, unicode(e))

        # Update the offering
        try:
            # Check if the user is the owner of the offering or if is a manager of the
            # owner organization
            if user.userprofile.current_organization != org \
                    or (not offering.is_owner(user) and user.pk not in org.managers):

                return build_response(request, 403, 'You are not allowed to edit the current offering')

            data = json.loads(request.raw_post_data)

            update_offering(user, offering, data)
        except Exception, e:
            return build_response(request, 400, e.message)

        return build_response(request, 200, 'OK')

    @authentication_required
    def delete(self, request, organization, name, version):
        # If the offering has been purchased it is not deleted
        # it is marked as deleted in order to allow customers that
        # have purchased the offering to install it if needed

        # Get the offering
        try:
            offering, org = _get_offering(organization, name, version)
        except ObjectDoesNotExist as e:
            return build_response(request, 404, unicode(e))
        except Exception as e:
            return build_response(request, 400, unicode(e))

        # Check if the user can delete the offering
        if request.user.userprofile.current_organization != org\
        or (not offering.is_owner(request.user) and not request.user.pk in org.managers):
            return build_response(request, 403, 'Forbidden')

        # Delete the offering
        try:
            delete_offering(request.user, offering)
        except Exception, e:
            return build_response(request, 400, unicode(e))

        return build_response(request, 204, 'No content')


class PublishEntry(Resource):

    # Publish the offering is some marketplaces
    @authentication_required
    @supported_request_mime_types(('application/json', 'application/xml'))
    def create(self, request, organization, name, version):
        # Obtains the offering
        offering = None
        content_type = get_content_type(request)[0]
        try:
            offering, org = _get_offering(organization, name, version)
        except ObjectDoesNotExist as e:
            return build_response(request, 404, unicode(e))
        except Exception as e:
            return build_response(request, 400, unicode(e))

        # Check that the user can publish the offering
        if request.user.userprofile.current_organization != org\
        or (not offering.is_owner(request.user) and not request.user.pk in org.managers):
            return build_response(request, 403, 'Forbidden')

        if content_type == 'application/json':
            try:
                data = json.loads(request.raw_post_data)
                publish_offering(request.user, offering, data)
            except HTTPError:
                return build_response(request, 502, 'The Marketplace has failed publishing the offering')
            except Exception, e:
                return build_response(request, 400, unicode(e))

        # Append the new offering to the newest list
        site = get_current_site(request)
        context = Context.objects.get(site=site)

        if len(context.newest) < 8:
            context.newest.insert(0, offering.pk)
        else:
            context.newest.pop()
            context.newest.insert(0, offering.pk)

        context.save()

        return build_response(request, 200, 'OK')


class NewestCollection(Resource):

    @authentication_required
    def read(self, request):

        site = get_current_site(request)
        context = Context.objects.get(site=site)

        response = []
        for off in context.newest:
            offering = Offering.objects.get(pk=off)
            response.append(get_offering_info(offering, request.user))

        return HttpResponse(json.dumps(response), status=200, mimetype='application/json')


class TopRatedCollection(Resource):

    @authentication_required
    def read(self, request):

        site = get_current_site(request)
        context = Context.objects.get(site=site)

        response = []
        for off in context.top_rated:
            offering = Offering.objects.get(pk=off)
            response.append(get_offering_info(offering, request.user))

        return HttpResponse(json.dumps(response), status=200, mimetype='application/json;charset=UTF-8')


####################################################################################################
#########################################    Resources   ###########################################
####################################################################################################


class ResourceCollection(Resource):

    # Creates a new resource associated with an user
    @supported_request_mime_types(('application/json', 'multipart/form-data'))
    @authentication_required
    def create(self, request):

        user = request.user
        profile = user.userprofile
        content_type = get_content_type(request)[0]

        if 'provider' in profile.get_current_roles():

            try:
                if content_type == 'application/json':
                    data = json.loads(request.raw_post_data)
                    register_resource(user, data)
                else:
                    data = json.loads(request.POST['json'])
                    f = request.FILES['file']
                    register_resource(user, data, file_=f)

            except ConflictError as e:
                return build_response(request, 409, unicode(e))
            except Exception as e:
                return build_response(request, 400, unicode(e))
        else:
            return build_response(request, 403, "You don't have the provider role")

        return build_response(request, 201, 'Created')

    @authentication_required
    def read(self, request):

        pagination = {
            'start': request.GET.get('start', None),
            'limit': request.GET.get('limit', None)
        }
        if pagination['start'] == None or pagination['limit'] == None:
            pagination = None

        profile = request.user.userprofile
        filter_ = request.GET.get('open', None)

        if filter_ and filter_ != 'true' and filter_ != 'false':
            return build_response(request, 400, 'Invalid open param')

        open_res = None
        if filter_ is not None:
            open_res = False

            if filter_ == 'true':
                open_res = True

        if 'provider' in profile.get_current_roles():
            try:
                response = get_provider_resources(request.user, filter_=open_res, pagination=pagination)
            except Exception, e:
                return build_response(request, 400, e.message)
        else:
            return build_response(request, 403, 'Forbidden')

        return HttpResponse(json.dumps(response), status=200, mimetype='application/json; charset=utf-8')


def _get_resource(resource_id_info):
    try:
        # Get the resource
        provider_org = Organization.objects.get(name=resource_id_info['provider'])
        resource = OfferingResource.objects.get(provider=provider_org, name=resource_id_info['name'], version=resource_id_info['version'])
    except:
        raise ValueError('Resource not found')

    return resource


def _call_resource_entry_method(request, resource_id_info, method, data, is_del=False):

    response = build_response(request, 200, 'OK')

    if is_del:
        response = build_response(request, 204, 'No Content')

    error = False

    try:
        resource = _get_resource(resource_id_info)
    except:
        error = True
        response = build_response(request, 404, 'Resource not found')

    # Check permissions
    if not error and ('provider' not in request.user.userprofile.get_current_roles() or
            not request.user.userprofile.current_organization == resource.provider):

        error = True
        response = build_response(request, 403, 'Forbidden')

    # Try to make the specified action
    if not error:
        try:
            args = (resource, ) + data
            method(*args)
        except Exception as e:
            response = build_response(request, 400, unicode(e))

    # Return the response
    return response


class ResourceEntry(Resource):

    @authentication_required
    def delete(self, request, provider, name, version):
        return _call_resource_entry_method(request, {
            'provider': provider,
            'name': name,
            'version': version
        }, delete_resource, (request.user, ), True)

    @supported_request_mime_types(('application/json', 'multipart/form-data'))
    @authentication_required
    def create(self, request, provider, name, version):

        content_type = get_content_type(request)[0]

        try:
            # Extract the data depending on the content type
            if content_type == 'application/json':
                data = json.loads(request.raw_post_data)
                params = (request.user, data, )
            else:
                data = json.loads(request.POST['json'])
                file_ = request.FILES['file']
                params = (request.user, data, file_)
        except:
            return build_response(request, 400, 'Invalid content')

        return _call_resource_entry_method(request, {
            'provider': provider,
            'name': name,
            'version': version
        }, upgrade_resource, params)

    @supported_request_mime_types(('application/json',))
    @authentication_required
    def update(self, request, provider, name, version):

        try:
            # Extract the data depending on the content type
            data = json.loads(request.raw_post_data)
            params = (request.user, data, )
        except:
            return build_response(request, 400, 'Invalid content')

        return _call_resource_entry_method(request, {
            'provider': provider,
            'name': name,
            'version': version
        }, update_resource, params)


class BindEntry(Resource):

    # Binds resources with offerings
    @authentication_required
    @supported_request_mime_types(('application/json',))
    def create(self, request, organization, name, version):
        # Obtains the offering
        offering = None
        try:
            offering, org = _get_offering(organization, name, version)
        except ObjectDoesNotExist as e:
            return build_response(request, 404, unicode(e))
        except Exception as e:
            return build_response(request, 400, unicode(e))

        # Check that the user can bind resources to the offering
        if request.user.userprofile.current_organization != org\
        or (not offering.is_owner(request.user) and not request.user.pk in org.managers):
            return build_response(request, 403, 'Forbidden')

        # Bind the resources
        try:
            data = json.loads(request.raw_post_data)
            bind_resources(offering, data, request.user)
        except Exception as e:
            return build_response(request, 400, unicode(e))

        return build_response(request, 200, 'OK')


####################################################################################################
##########################################    Reviews    ###########################################
####################################################################################################

def _make_review_action(action, request, organization, name, version, review=None):
    """
    Performs the specified review action
    """

    # Get the offering
    try:
        offering, org = _get_offering(organization, name, version)
    except ObjectDoesNotExist as e:
        return build_response(request, 404, unicode(e))
    except Exception as e:
        return build_response(request, 400, unicode(e))

    # Check offering state
    if offering.state != 'published':
        return build_response(request, 403, 'Forbidden')

    # Call the action method
    try:
        data = None
        if request.raw_post_data:
            data = json.loads(request.raw_post_data)

        # Check if the param to be used in the call
        # is the offering or the review
        param = offering
        if review:
            param = review

        # Only include data in the call if included in
        # the request
        if data:
            action(request.user, param, data)
        else:
            action(request.user, param)

    except PermissionDenied as e:
        return build_response(request, 403, unicode(e))
    except Exception as e:
        return build_response(request, 400, unicode(e))

    return build_response(request, 201, 'Created')


class ReviewCollection(Resource):

    @authentication_required
    def read(self, request, organization, name, version):
        # Get pagination params
        start = request.GET.get('start', None)
        limit = request.GET.get('limit', None)

        # Get offering
        try:
            offering, org = _get_offering(organization, name, version)
        except ObjectDoesNotExist as e:
            return build_response(request, 404, unicode(e))
        except Exception as e:
            return build_response(request, 400, unicode(e))

        # Get reviews
        rm = ReviewManager()
        try:
            response = rm.get_reviews(offering, start=start, limit=limit)
        except PermissionDenied as e:
            return build_response(request, 403, unicode(e))
        except Exception as e:
            return build_response(request, 400, unicode(e))

        return HttpResponse(json.dumps(response), status=200, mimetype='application/json; charset=utf-8')

    @authentication_required
    @supported_request_mime_types(('application/json', ))
    def create(self, request, organization, name, version):

        rm = ReviewManager()
        return _make_review_action(rm.create_review, request, organization, name, version)


class ReviewEntry(Resource):

    @authentication_required
    @supported_request_mime_types(('application/json', ))
    def update(self, request, organization, name, version, review):

        rm = ReviewManager()
        return _make_review_action(rm.update_review, request, organization, name, version, review=review)

    @authentication_required
    def delete(self, request, organization, name, version, review):
        rm = ReviewManager()
        return _make_review_action(rm.remove_review, request, organization, name, version, review=review)


class ResponseEntry(Resource):

    @authentication_required
    @supported_request_mime_types(('application/json', ))
    def update(self, request, organization, name, version, review):
        rm = ReviewManager()
        return _make_review_action(rm.create_response, request, organization, name, version, review=review)

    @authentication_required
    def delete(self, request, organization, name, version, review):
        rm = ReviewManager()
        return _make_review_action(rm.remove_response, request, organization, name, version, review=review)


####################################################################################################
#######################################    Applications    #########################################
####################################################################################################


class ApplicationCollection(Resource):

    # Get idm applications
    @authentication_required
    @identity_manager_required
    def read(self, request):

        # Check user roles
        if 'provider' not in request.user.userprofile.get_current_roles():
            return build_response(request, 403, 'Forbidden')

        resp = get_applications(request.user)

        return HttpResponse(resp, status=200, mimetype='application/json;charset=UTF-8')
